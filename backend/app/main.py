from fastapi import FastAPI
from sqlalchemy import text

from app.config import get_settings
from app.db.session import SessionLocal

app = FastAPI(title="AI Paper Reader", version="0.1.0")


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
