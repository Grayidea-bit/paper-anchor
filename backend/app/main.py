from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import get_settings
from app.db import repo
from app.db.session import SessionLocal
from app.errors import AppError
from app.routers import conversations, documents

app = FastAPI(title="AI Paper Reader", version="0.1.0")
app.include_router(documents.router)
app.include_router(conversations.router)


@app.exception_handler(AppError)
async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


@app.get("/api/usage")
async def total_usage() -> dict:
    async with SessionLocal() as session:
        return await repo.total_token_usage(session)


@app.get("/healthz")
async def healthz() -> dict:
    settings = get_settings()
    db_ok = False
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    return {
        "status": "ok",
        "db": db_ok,
        "chat_model": settings.llm_chat_model,
        "llm_key_set": bool(settings.llm_api_key),
    }
