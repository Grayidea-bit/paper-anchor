"""services/gdrive.py 測試：OAuth（PKCE/state）、token 刷新、Drive REST 重試路徑。

不打真 API：以 httpx.MockTransport 攔截請求（gdrive 內部 `httpx.AsyncClient(...)`
自建 client，故 patch 模組層 `httpx.AsyncClient` 注入 transport）。退避 sleep 一律
monkeypatch 免真等。
"""

import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app import settings_store
from app.services import gdrive

_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _install(monkeypatch, handler):
    """把 gdrive 內部建立的 AsyncClient 綁到 MockTransport(handler)。"""
    transport = httpx.MockTransport(handler)

    def factory(*args, **kwargs):
        kwargs.pop("transport", None)
        return _REAL_ASYNC_CLIENT(*args, transport=transport, **kwargs)

    monkeypatch.setattr(gdrive.httpx, "AsyncClient", factory)


def _form(request: httpx.Request) -> dict:
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    """每測試重置 OAuth 記憶體狀態與 no-wait 退避。"""
    monkeypatch.setattr(
        settings_store,
        "_cache",
        {
            "gdrive_client_id": "cid.apps.googleusercontent.com",
            "gdrive_client_secret": "csecret",
            "gdrive_refresh_token": "rtoken",
        },
    )
    gdrive._pending.clear()
    gdrive._access_token = None
    gdrive._access_expires_at = 0.0

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(gdrive.asyncio, "sleep", _no_sleep)
    yield
    gdrive._pending.clear()
    gdrive._access_token = None
    gdrive._access_expires_at = 0.0


def _prime_access_token():
    """預塞有效 access token，讓 Drive 呼叫免走 token 端點。"""
    gdrive._access_token = "cached-access"
    gdrive._access_expires_at = time.monotonic() + 3600


# ---------- OAuth：授權網址 ----------


class TestBuildAuthUrl:
    def test_contains_pkce_and_state(self):
        url = gdrive.build_auth_url()
        qs = parse_qs(urlparse(url).query)
        assert qs["scope"] == [gdrive.SCOPE]
        assert qs["access_type"] == ["offline"]
        assert qs["prompt"] == ["consent"]
        assert qs["code_challenge_method"] == ["S256"]
        assert qs["response_type"] == ["code"]
        assert qs["redirect_uri"] == [gdrive.DEFAULT_REDIRECT_URI]
        state = qs["state"][0]
        # state 已暫存待 callback 驗證，且對應一個 PKCE verifier
        assert state in gdrive._pending
        assert qs["code_challenge"][0]  # 非空

    def test_missing_client_id_raises(self, monkeypatch):
        monkeypatch.setattr(settings_store, "_cache", {})
        with pytest.raises(gdrive.GDriveAuthError) as exc:
            gdrive.build_auth_url()
        assert exc.value.code == "client_id_unset"


# ---------- OAuth：授權碼換 token ----------


class TestExchangeCode:
    async def test_success_sends_pkce_verifier(self, monkeypatch):
        gdrive._pending["st1"] = "the-verifier"
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            assert str(request.url) == gdrive.TOKEN_URI
            seen.update(_form(request))
            return httpx.Response(
                200,
                json={
                    "refresh_token": "new-refresh",
                    "access_token": "new-access",
                    "expires_in": 3600,
                },
            )

        _install(monkeypatch, handler)
        refresh = await gdrive.exchange_code("auth-code", "st1")

        assert refresh == "new-refresh"
        assert seen["code_verifier"] == "the-verifier"
        assert seen["grant_type"] == "authorization_code"
        assert seen["code"] == "auth-code"
        # state 用後即棄
        assert "st1" not in gdrive._pending
        # access token 一併快取
        assert gdrive._access_token == "new-access"

    async def test_state_mismatch_raises(self, monkeypatch):
        _install(monkeypatch, lambda r: httpx.Response(200, json={}))
        with pytest.raises(gdrive.GDriveAuthError) as exc:
            await gdrive.exchange_code("code", "unknown-state")
        assert exc.value.code == "invalid_state"

    async def test_no_refresh_token_raises(self, monkeypatch):
        gdrive._pending["st2"] = "v"
        _install(
            monkeypatch,
            lambda r: httpx.Response(200, json={"access_token": "a", "expires_in": 3600}),
        )
        with pytest.raises(gdrive.GDriveError):
            await gdrive.exchange_code("code", "st2")


# ---------- OAuth：刷新 access token ----------


class TestRefreshAccessToken:
    async def test_success_and_cache(self, monkeypatch):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            form = _form(request)
            assert form["grant_type"] == "refresh_token"
            assert form["refresh_token"] == "rtoken"
            return httpx.Response(200, json={"access_token": "fresh", "expires_in": 3600})

        _install(monkeypatch, handler)
        assert await gdrive.refresh_access_token() == "fresh"
        # 第二次命中記憶體快取，不再打端點
        assert await gdrive.refresh_access_token() == "fresh"
        assert calls["n"] == 1

    async def test_invalid_grant_disconnects(self, monkeypatch):
        _install(
            monkeypatch,
            lambda r: httpx.Response(400, json={"error": "invalid_grant"}),
        )
        with pytest.raises(gdrive.GDriveDisconnectedError):
            await gdrive.refresh_access_token()
        assert gdrive._access_token is None

    async def test_missing_refresh_token_disconnects(self, monkeypatch):
        monkeypatch.setattr(settings_store, "_cache", {"gdrive_client_id": "c"})
        with pytest.raises(gdrive.GDriveDisconnectedError):
            await gdrive.refresh_access_token()


# ---------- Drive REST：資料夾 ----------


class TestEnsureFolder:
    async def test_returns_existing(self, monkeypatch):
        _prime_access_token()

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "GET"
            return httpx.Response(200, json={"files": [{"id": "fid", "name": "Backup"}]})

        _install(monkeypatch, handler)
        assert await gdrive.ensure_folder("Backup") == "fid"

    async def test_creates_when_absent(self, monkeypatch):
        _prime_access_token()

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, json={"files": []})
            return httpx.Response(200, json={"id": "created-id"})

        _install(monkeypatch, handler)
        assert await gdrive.ensure_folder("Backup") == "created-id"


# ---------- Drive REST：resumable 上傳 ----------


class TestUploadFile:
    async def test_two_step_resumable(self, monkeypatch):
        _prime_access_token()
        session_url = "https://upload.example/session/abc"
        steps: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                steps.append("init")
                assert "uploadType=resumable" in str(request.url)
                assert request.headers["X-Upload-Content-Type"] == "application/pdf"
                return httpx.Response(200, headers={"Location": session_url})
            if request.method == "PUT":
                steps.append("put")
                assert str(request.url) == session_url
                assert request.content == b"PDFBYTES"
                return httpx.Response(200, json={"id": "file-1", "name": "x.pdf"})
            raise AssertionError(request.method)

        _install(monkeypatch, handler)
        result = await gdrive.upload_file("folder", "x.pdf", b"PDFBYTES", "application/pdf")

        assert steps == ["init", "put"]
        assert result == {"id": "file-1", "name": "x.pdf"}

    async def test_upload_streams_from_path(self, monkeypatch, tmp_path):
        _prime_access_token()
        pdf = tmp_path / "big.pdf"
        pdf.write_bytes(b"streamed-content")
        session_url = "https://upload.example/session/xyz"

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                return httpx.Response(201, headers={"Location": session_url})
            assert request.content == b"streamed-content"
            return httpx.Response(200, json={"id": "f2"})

        _install(monkeypatch, handler)
        result = await gdrive.upload_file("folder", "big.pdf", str(pdf), "application/pdf")
        assert result["id"] == "f2"


# ---------- Drive REST：重試與 401 刷新 ----------


class TestRetryAndRefresh:
    async def test_429_backoff_then_success(self, monkeypatch):
        _prime_access_token()
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(429, json={"error": "rate"})
            return httpx.Response(200, json={"files": [{"id": "a", "name": "a.pdf"}]})

        _install(monkeypatch, handler)
        files = await gdrive.list_folder("folder")
        assert calls["n"] == 2
        assert files == [{"id": "a", "name": "a.pdf"}]

    async def test_401_refreshes_then_retries(self, monkeypatch):
        # 有 refresh token 但無快取 access → 首次 Drive 用刷新後的 token；
        # 模擬該 token 失效（401），強制刷新換新 token 再成功。
        gdrive._access_token = "stale"
        gdrive._access_expires_at = time.monotonic() + 3600
        seq: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == gdrive.TOKEN_URI:
                seq.append("token")
                return httpx.Response(200, json={"access_token": "fresh", "expires_in": 3600})
            auth = request.headers["Authorization"]
            seq.append(auth)
            if auth == "Bearer stale":
                return httpx.Response(401, json={"error": "unauth"})
            return httpx.Response(200, json={"files": []})

        _install(monkeypatch, handler)
        result = await gdrive.list_folder("folder")
        assert result == []
        # 順序：帶 stale → 401 → 換 token → 帶 fresh → 200
        assert seq == ["Bearer stale", "token", "Bearer fresh"]

    async def test_persistent_5xx_raises(self, monkeypatch):
        _prime_access_token()
        _install(monkeypatch, lambda r: httpx.Response(503, text="unavailable"))
        with pytest.raises(gdrive.GDriveError):
            await gdrive.list_folder("folder")


# ---------- update_file ----------


class TestUpdateFile:
    async def test_media_overwrite(self, monkeypatch):
        _prime_access_token()

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "PATCH"
            assert "uploadType=media" in str(request.url)
            assert "file-9" in str(request.url)
            assert request.content == b"{}"
            return httpx.Response(200, json={"id": "file-9"})

        _install(monkeypatch, handler)
        result = await gdrive.update_file("file-9", b"{}", "application/json")
        assert result["id"] == "file-9"


# ---------- 安全：例外訊息不得含 token ----------


class TestSecretSafety:
    async def test_error_message_excludes_token(self, monkeypatch):
        _prime_access_token()
        _install(monkeypatch, lambda r: httpx.Response(500, text="boom"))
        with pytest.raises(gdrive.GDriveError) as exc:
            await gdrive.list_folder("folder")
        assert "cached-access" not in str(exc.value)
        assert "rtoken" not in str(exc.value)
