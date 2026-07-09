import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app import settings_store
from app.db import repo
from app.db.session import SessionLocal
from app.errors import AppError
from app.llm import _chat_config, current_rpm
from app.routers import (
    annotations,
    backup,
    conversations,
    documents,
    glossary,
    projects,
)
from app.routers import (
    settings as settings_router,
)
from app.routers import (
    tools as tools_router,
)
from app.services.backup_scheduler import scheduler_loop
from app.version import APP_VERSION

logger = logging.getLogger(__name__)


def _warn_if_multi_worker() -> None:
    """多 worker 防呆（M15 T-FD-04）：偵測到 >1 worker 即記警告。

    backup/restore/reingest 互斥鎖與 `settings_store` 設定快取皆為模組級（per-process）狀態，
    不跨行程共享。多 worker 下併發互斥失效（可能同時跑兩份備份／還原）、設定更新只對接到請求
    的那個 worker 生效。本系統設計為單 worker（見 docs/02-architecture.md 部署假設）。
    輕量偵測：讀 gunicorn/uvicorn 慣用的 `WEB_CONCURRENCY` 環境變數。
    盡力而為的偵測：`uvicorn --workers N` CLI 不設此變數，該路徑偵測不到（無可靠跨行程訊號）
    ——防線以文件（部署假設）為主，本警告只攔 gunicorn/WEB_CONCURRENCY 慣例路徑。
    """
    raw = os.getenv("WEB_CONCURRENCY", "").strip()
    try:
        workers = int(raw) if raw else 1
    except ValueError:
        workers = 1
    if workers > 1:
        logger.warning(
            "偵測到 WEB_CONCURRENCY=%s（多 worker）：backup/restore/reingest 互斥鎖與 "
            "settings_store 快取為 per-process 狀態，多 worker 下併發互斥失效、設定更新只對單一 "
            "worker 生效。本系統設計為單 worker，請勿以多 worker 啟動（見 docs/02-architecture.md "
            "部署假設）。",
            raw,
        )


@asynccontextmanager
async def lifespan(_: FastAPI):
    _warn_if_multi_worker()
    try:
        await settings_store.ensure_loaded()
    except Exception:
        pass  # DB 未就緒時延後到第一次 API 呼叫
    try:
        # 啟動時自癒（M15 T-FD-01 / D4）：上一輪被中斷的 ingest（uploaded/parsing/embedding，
        # 含 restore ingest phase 中斷時停在 uploaded 的整批）轉 failed，
        # 使其可經 /reingest 端點救回而非永久卡住。DB 未就緒時容錯略過（同上）。
        async with SessionLocal() as session:
            n = await repo.reconcile_interrupted_ingests(session)
        if n:
            logger.warning("lifespan: reconciled %d interrupted ingest(s) to failed", n)
    except Exception:
        pass
    task = asyncio.create_task(scheduler_loop())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="Paper Anchor", version=APP_VERSION, lifespan=lifespan)
app.include_router(documents.router)
app.include_router(annotations.router)
app.include_router(glossary.router)
app.include_router(conversations.router)
app.include_router(projects.router)
app.include_router(settings_router.router)
app.include_router(tools_router.router)
app.include_router(backup.router)


@app.exception_handler(AppError)
async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


@app.get("/api/usage")
async def total_usage() -> dict:
    async with SessionLocal() as session:
        usage = await repo.total_token_usage(session)
    usage["rpm"] = current_rpm()
    return usage


@app.get("/healthz")
async def healthz() -> dict:
    db_ok = False
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    _, api_key, chat_model = _chat_config()  # settings 覆蓋後的實際生效值
    return {
        "status": "ok",
        "db": db_ok,
        "chat_model": chat_model,
        "llm_key_set": bool(api_key),
    }
