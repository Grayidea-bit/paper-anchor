"""Google Drive 存取唯一邊界（M12 單向備份，CLAUDE.md 供應商邊界同理）。

本模組收束「OAuth（loopback + PKCE）」與「Drive REST 窄介面（4 函式）」，
上層 services/backup.py 與 routers/backup.py 只透過這裡與 Drive 溝通；未來若換
rclone 實作亦只動這一層（見 docs/02-architecture.md D10）。

安全鐵律：
- refresh token 存 settings_store（SECRET_KEYS 遮罩），access token 只留記憶體。
- 任何 log／例外訊息一律不得含 token、code、client_secret。
"""

import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
import time
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlencode

import httpx

from app import settings_store
from app.config import get_settings
from app.errors import AppError

logger = logging.getLogger(__name__)

# ---------- 常數 ----------

AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
FILES_URL = "https://www.googleapis.com/drive/v3/files"
UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
SCOPE = "https://www.googleapis.com/auth/drive.file"
FOLDER_MIME = "application/vnd.google-apps.folder"
DEFAULT_REDIRECT_URI = "http://localhost:8000/api/backup/auth/callback"

_MAX_ATTEMPTS = 4
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
_ACCESS_TOKEN_SKEW = 60  # 秒；提前視為過期，避免臨界失敗
_UPLOAD_CHUNK = 1024 * 1024  # 1 MiB 串流分塊
_REQUEST_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
_UPLOAD_TIMEOUT = httpx.Timeout(300.0, connect=10.0)
_PENDING_CAP = 128  # 未完成授權暫存上限，超過即清空防記憶體累積


# ---------- 例外 ----------


class GDriveError(AppError):
    """Drive 存取失敗（網路／API 非預期回應）。訊息不得含 token。"""

    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__("gdrive_error", message, status=status)


class GDriveAuthError(AppError):
    """OAuth 流程錯誤（state 不符、client 未設定等）。"""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(code, message, status=status)


class GDriveDisconnectedError(AppError):
    """refresh token 缺失或失效（invalid_grant）→ 需重新連接。"""

    def __init__(self, message: str = "Google Drive 尚未連接或授權已失效，請重新連接") -> None:
        super().__init__("gdrive_disconnected", message, status=400)


# ---------- OAuth 狀態（記憶體） ----------

# state -> PKCE code_verifier；callback 驗證後即 pop。單機單使用者，模組級即可。
_pending: dict[str, str] = {}

# access token 記憶體快取（過期即以 refresh token 換新）
_access_token: str | None = None
_access_expires_at: float = 0.0


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _redirect_uri() -> str:
    """由設定組出；未設定時回落預設 loopback callback。"""
    return getattr(get_settings(), "backup_redirect_uri", None) or DEFAULT_REDIRECT_URI


def _oauth_client() -> tuple[str, str]:
    """(client_id, client_secret)；缺任一即視為未設定。"""
    client_id = settings_store.runtime("gdrive_client_id")
    client_secret = settings_store.runtime("gdrive_client_secret")
    if not client_id or not client_secret:
        raise GDriveAuthError(
            "client_id_unset", "請先在設定頁填入 Google OAuth client_id 與 client_secret"
        )
    return client_id, client_secret


# ---------- OAuth 三函式 ----------


def build_auth_url() -> str:
    """產生授權網址（含 state + PKCE challenge）；state/verifier 暫存待 callback 驗證。

    未設定 gdrive_client_id → GDriveAuthError(code="client_id_unset")。
    """
    client_id = settings_store.runtime("gdrive_client_id")
    if not client_id:
        raise GDriveAuthError("client_id_unset", "請先填入 Google OAuth client_id")

    if len(_pending) >= _PENDING_CAP:
        _pending.clear()

    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(64)  # 43–128 字元符合 PKCE 規範
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    _pending[state] = verifier

    params = {
        "client_id": client_id,
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{AUTH_URI}?{urlencode(params)}"


async def exchange_code(code: str, state: str) -> str:
    """驗 state → 以 PKCE verifier 換 token，回傳 refresh_token。

    state 不符 → GDriveAuthError(code="invalid_state")。
    回應無 refresh_token → GDriveError（多半是缺 prompt=consent 或非 offline）。
    """
    verifier = _pending.pop(state, None)
    if verifier is None:
        raise GDriveAuthError("invalid_state", "OAuth state 不符或已過期，請重新發起連接")

    client_id, client_secret = _oauth_client()
    data = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": _redirect_uri(),
        "grant_type": "authorization_code",
        "code_verifier": verifier,
    }
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
        resp = await client.post(TOKEN_URI, data=data)
    if resp.status_code != 200:
        raise GDriveError(f"授權碼換 token 失敗（HTTP {resp.status_code}: {_token_err(resp)}）")

    body = resp.json()
    refresh_token = body.get("refresh_token")
    if not refresh_token:
        raise GDriveError("Google 未回傳 refresh_token（請確認授權時已同意離線存取）")

    _store_access_token(body)
    return refresh_token


async def refresh_access_token(*, force: bool = False) -> str:
    """以 settings 的 gdrive_refresh_token 換 access token（記憶體快取，含過期時間）。

    invalid_grant（refresh token 失效）→ GDriveDisconnectedError。
    """
    global _access_token
    now = time.monotonic()
    if not force and _access_token and _access_expires_at > now:
        return _access_token

    refresh_token = settings_store.runtime("gdrive_refresh_token")
    if not refresh_token:
        raise GDriveDisconnectedError()

    client_id, client_secret = _oauth_client()
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
        resp = await client.post(TOKEN_URI, data=data)

    if resp.status_code == 400 and _token_err(resp) == "invalid_grant":
        _access_token = None
        raise GDriveDisconnectedError("Google Drive 授權已失效（invalid_grant），請重新連接")
    if resp.status_code != 200:
        raise GDriveError(f"刷新 access token 失敗（HTTP {resp.status_code}: {_token_err(resp)}）")

    return _store_access_token(resp.json())


def _store_access_token(body: dict) -> str:
    global _access_token, _access_expires_at
    token = body.get("access_token")
    if not token:
        raise GDriveError("token 回應缺 access_token")
    expires_in = int(body.get("expires_in", 3600))
    _access_token = token
    _access_expires_at = time.monotonic() + max(expires_in - _ACCESS_TOKEN_SKEW, 0)
    return token


def _token_err(resp: httpx.Response) -> str:
    """從 token 端點回應取 `error` 欄位（非秘密，如 invalid_grant）；取不到回空字串。"""
    try:
        err = resp.json().get("error")
    except (ValueError, AttributeError):
        return ""
    return err if isinstance(err, str) else ""


# ---------- Drive REST 共用（帶重試 + 401 刷新） ----------


async def _authed(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    content_factory: Any = None,
    **kwargs: Any,
) -> httpx.Response:
    """帶 Bearer 的請求：429/5xx 指數退避重試、401 刷新 token 後重試一次。

    content_factory：可呼叫物件，每次嘗試回傳新的 body（供串流重試重建 generator）。
    """
    token = await refresh_access_token()
    refreshed = False
    resp: httpx.Response | None = None
    for attempt in range(_MAX_ATTEMPTS):
        call_headers = {"Authorization": f"Bearer {token}"}
        if headers:
            call_headers.update(headers)
        call_kwargs = dict(kwargs)
        if content_factory is not None:
            call_kwargs["content"] = content_factory()
        resp = await client.request(method, url, headers=call_headers, **call_kwargs)

        if resp.status_code == 401 and not refreshed:
            token = await refresh_access_token(force=True)
            refreshed = True
            continue
        if resp.status_code in _RETRY_STATUS and attempt < _MAX_ATTEMPTS - 1:
            await _backoff(attempt)
            continue
        return resp
    assert resp is not None  # 迴圈至少執行一次
    return resp


async def _backoff(attempt: int) -> None:
    await asyncio.sleep(2**attempt)


def _require_ok(resp: httpx.Response, action: str, *ok: int) -> None:
    ok_codes = ok or (200,)
    if resp.status_code not in ok_codes:
        # Drive API 錯誤 body 不含 token，可截斷附上助除錯
        raise GDriveError(f"{action} 失敗（HTTP {resp.status_code}: {resp.text[:200]}）")


def _escape(value: str) -> str:
    """Drive query 字串字面值轉義（單引號與反斜線）。"""
    return value.replace("\\", "\\\\").replace("'", "\\'")


# ---------- Drive REST 四函式 ----------


async def ensure_folder(name: str, parent_id: str | None = None) -> str:
    """查同名資料夾（未 trash），無則建立，回傳 folder id。"""
    query = f"mimeType='{FOLDER_MIME}' and name='{_escape(name)}' and trashed=false"
    if parent_id:
        query += f" and '{_escape(parent_id)}' in parents"
    params = {
        "q": query,
        "fields": "files(id,name)",
        "spaces": "drive",
        "pageSize": "1",
    }
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
        resp = await _authed(client, "GET", FILES_URL, params=params)
        _require_ok(resp, "查詢資料夾")
        files = resp.json().get("files", [])
        if files:
            return files[0]["id"]

        metadata: dict[str, Any] = {"name": name, "mimeType": FOLDER_MIME}
        if parent_id:
            metadata["parents"] = [parent_id]
        resp = await _authed(
            client,
            "POST",
            FILES_URL,
            headers={"Content-Type": "application/json; charset=UTF-8"},
            params={"fields": "id"},
            content=json.dumps(metadata),
        )
        _require_ok(resp, "建立資料夾", 200, 201)
        return resp.json()["id"]


async def list_folder(folder_id: str) -> list[dict]:
    """分頁全取資料夾內檔案，回傳 [{id, name, md5Checksum, size}, ...]。"""
    files: list[dict] = []
    page_token: str | None = None
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
        while True:
            params = {
                "q": f"'{_escape(folder_id)}' in parents and trashed=false",
                "fields": "nextPageToken, files(id,name,md5Checksum,size)",
                "spaces": "drive",
                "pageSize": "1000",
            }
            if page_token:
                params["pageToken"] = page_token
            resp = await _authed(client, "GET", FILES_URL, params=params)
            _require_ok(resp, "列出資料夾")
            data = resp.json()
            files.extend(data.get("files", []))
            page_token = data.get("nextPageToken")
            if not page_token:
                break
    return files


async def upload_file(
    folder_id: str, name: str, content_or_path: bytes | str | os.PathLike, mime: str
) -> dict:
    """resumable 兩段式上傳新檔（POST 起 session → PUT 內容），回傳檔案資源。

    content_or_path：bytes 直接上傳；str/PathLike 視為檔案路徑串流（支援大檔）。
    """
    content_factory = _content_factory(content_or_path)
    metadata = json.dumps({"name": name, "parents": [folder_id]})

    async with httpx.AsyncClient(timeout=_UPLOAD_TIMEOUT) as client:
        # 第一段：起 resumable session
        init = await _authed(
            client,
            "POST",
            f"{UPLOAD_URL}?uploadType=resumable",
            headers={
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Type": mime,
            },
            content=metadata,
        )
        _require_ok(init, "起始 resumable 上傳", 200, 201)
        session_url = init.headers.get("Location") or init.headers.get("location")
        if not session_url:
            raise GDriveError("resumable session URL 缺失（回應無 Location header）")

        # 第二段：PUT 內容
        resp = await _authed(
            client,
            "PUT",
            session_url,
            headers={"Content-Type": mime},
            content_factory=content_factory,
        )
        _require_ok(resp, "上傳檔案內容", 200, 201)
        return resp.json()


async def update_file(file_id: str, content: bytes, mime: str) -> dict:
    """以 media 簡單上傳覆蓋既有檔內容（同一 file id），回傳檔案資源。"""
    async with httpx.AsyncClient(timeout=_UPLOAD_TIMEOUT) as client:
        resp = await _authed(
            client,
            "PATCH",
            f"{UPLOAD_URL}/{file_id}?uploadType=media",
            headers={"Content-Type": mime},
            content_factory=lambda: bytes(content),
        )
        _require_ok(resp, "覆蓋檔案內容", 200, 201)
        return resp.json()


def _content_factory(content_or_path: bytes | str | os.PathLike) -> Any:
    """回傳每次呼叫都產生新 body 的工廠（支援重試時重建串流）。"""
    if isinstance(content_or_path, (bytes, bytearray)):
        data = bytes(content_or_path)
        return lambda: data
    path = os.fspath(content_or_path)
    return lambda: _file_stream(path)


async def _file_stream(path: str) -> AsyncIterator[bytes]:
    # httpx 的 AsyncClient 串流 body 需 async iterator（大檔分塊，避免整檔進記憶體）；
    # 檔案 I/O 一律經 to_thread 卸載，不阻塞事件迴圈。
    f = await asyncio.to_thread(open, path, "rb")
    try:
        while True:
            chunk = await asyncio.to_thread(f.read, _UPLOAD_CHUNK)
            if not chunk:
                break
            yield chunk
    finally:
        await asyncio.to_thread(f.close)
