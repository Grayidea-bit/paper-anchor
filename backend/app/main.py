from contextlib import asynccontextmanager

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


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        await settings_store.ensure_loaded()
    except Exception:
        pass  # DB 未就緒時延後到第一次 API 呼叫
    yield


app = FastAPI(title="Paper Anchor", version="0.1.0", lifespan=lifespan)
app.include_router(documents.router)
app.include_router(annotations.router)
app.include_router(glossary.router)
app.include_router(conversations.router)
app.include_router(projects.router)
app.include_router(settings_router.router)
app.include_router(tools_router.router)


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
