"""單向備份到 Google Drive 端點（M12 D10 / T-BK-03）。

薄層：狀態/觸發/OAuth loopback 全部委派 services/backup.py 與 services/gdrive.py，
本檔不含業務邏輯。錯誤走既有 AppError handler（見 app/main.py），callback 端點是
瀏覽器導向的普通頁面（非 JSON API），成功/Google 端拒絕時各回一頁極簡 HTML。
"""

import html

from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import HTMLResponse

from app import settings_store
from app.errors import AppError
from app.services import backup, restore
from app.services.gdrive import build_auth_url, exchange_code, forget_access_token

router = APIRouter(prefix="/api/backup", tags=["backup"])

_CONNECTED_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Google Drive 已連接</title></head>
<body style="font-family: sans-serif; text-align: center; padding-top: 4rem;">
<h2>已連接 Google Drive</h2>
<p>可關閉此分頁。</p>
</body></html>"""

_FAILED_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Google Drive 連接失敗</title></head>
<body style="font-family: sans-serif; text-align: center; padding-top: 4rem;">
<h2>連接失敗</h2>
<p>{message}</p>
</body></html>"""


@router.get("/status")
async def get_backup_status() -> dict:
    return await backup.get_status()


@router.post("/run", status_code=202)
async def trigger_backup_run(background_tasks: BackgroundTasks) -> dict:
    if not settings_store.runtime("gdrive_refresh_token"):
        raise AppError("not_connected", "尚未連接 Google Drive", status=400)
    if backup.is_running():
        raise AppError("backup_running", "備份已在進行中", status=409)
    background_tasks.add_task(backup.run_backup)
    return {"started": True}


@router.post("/restore", status_code=202)
async def trigger_restore(background_tasks: BackgroundTasks) -> dict:
    """從 Drive 匯入還原（M13 D11）；未連接 400、已有備份/還原進行中 409 operation_running。

    遠端無備份/格式不符等於還原執行時偵測，結果經 `status` 的 `last_restore` 回報。
    """
    if not settings_store.runtime("gdrive_refresh_token"):
        raise AppError("not_connected", "尚未連接 Google Drive", status=400)
    if backup.is_running():
        raise AppError("operation_running", "已有備份或還原進行中", status=409)
    background_tasks.add_task(restore.run_restore)
    return {"started": True}


@router.get("/auth/start")
async def start_backup_auth() -> dict:
    return {"auth_url": build_auth_url()}


@router.get("/auth/callback")
async def backup_auth_callback(
    code: str | None = None, state: str | None = None, error: str | None = None
) -> HTMLResponse:
    """OAuth loopback 回呼；供瀏覽器導向，非 JSON API——一律回 HTML。

    Google 端拒絕授權時帶 `error` 參數 → 失敗頁；我方 state 驗證失敗或換 token
    失敗（`exchange_code` 拋 AppError 子類）也統一渲染失敗頁，不回裸 JSON。
    """
    if error:
        return HTMLResponse(_FAILED_HTML.format(message=html.escape(error)), status_code=400)

    try:
        refresh_token = await exchange_code(code or "", state or "")
    except AppError as exc:
        return HTMLResponse(_FAILED_HTML.format(message=html.escape(exc.message)), status_code=400)
    await settings_store.update({"gdrive_refresh_token": refresh_token})
    return HTMLResponse(_CONNECTED_HTML)


@router.post("/auth/disconnect", status_code=204)
async def disconnect_backup_auth() -> None:
    """中斷連接、清除 refresh token；不刪除遠端任何資料（D10 刪除語意）。"""
    await settings_store.update({"gdrive_refresh_token": ""})
    forget_access_token()  # 防禦縱深：不留記憶體中仍有效約 1 小時的 access token
