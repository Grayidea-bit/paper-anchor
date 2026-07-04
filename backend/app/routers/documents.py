import uuid
from pathlib import Path

import aiofiles
from anyio import Path as AsyncPath
from fastapi import APIRouter, BackgroundTasks, UploadFile
from fastapi.responses import FileResponse

from app.config import get_settings
from app.db import repo
from app.db.session import SessionLocal
from app.errors import AppError, NotFoundError
from app.services.digest import generate_digest
from app.services.ingest import ingest_document

router = APIRouter(prefix="/api/documents", tags=["documents"])

_WRITE_CHUNK = 1024 * 1024


@router.post("", status_code=201)
async def upload_document(file: UploadFile, background_tasks: BackgroundTasks) -> dict:
    if not (file.filename or "").lower().endswith(".pdf"):
        raise AppError("invalid_file", "僅支援 PDF 檔案")
    settings = get_settings()
    upload_dir = AsyncPath(settings.upload_dir)
    await upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / f"{uuid.uuid4().hex}.pdf"

    max_bytes = settings.max_upload_mb * 1024 * 1024
    written = 0
    async with aiofiles.open(str(dest), "wb") as f:
        while chunk := await file.read(_WRITE_CHUNK):
            written += len(chunk)
            if written > max_bytes:
                await f.close()
                await dest.unlink(missing_ok=True)
                raise AppError("file_too_large", f"檔案超過 {settings.max_upload_mb}MB 上限")
            await f.write(chunk)
    if written == 0:
        await dest.unlink(missing_ok=True)
        raise AppError("empty_file", "檔案是空的")

    async with SessionLocal() as session:
        doc = await repo.create_document(session, file.filename or dest.name, str(dest))
    background_tasks.add_task(ingest_document, doc["id"])
    return doc


@router.get("")
async def list_documents() -> list[dict]:
    async with SessionLocal() as session:
        return await repo.list_documents(session)


@router.get("/{doc_id}")
async def get_document(doc_id: int) -> dict:
    async with SessionLocal() as session:
        doc = await repo.get_document(session, doc_id)
    if doc is None:
        raise NotFoundError("document", doc_id)
    doc.pop("file_path", None)
    return doc


@router.get("/{doc_id}/file")
async def get_document_file(doc_id: int) -> FileResponse:
    async with SessionLocal() as session:
        doc = await repo.get_document(session, doc_id)
    if doc is None:
        raise NotFoundError("document", doc_id)
    path = Path(doc["file_path"])
    if not await AsyncPath(path).exists():
        raise AppError("file_missing", "PDF 檔案遺失", status=410)
    return FileResponse(path, media_type="application/pdf", filename=doc["filename"])


@router.get("/{doc_id}/chunks")
async def get_document_chunks(doc_id: int, limit: int = 500) -> list[dict]:
    async with SessionLocal() as session:
        doc = await repo.get_document(session, doc_id)
        if doc is None:
            raise NotFoundError("document", doc_id)
        return await repo.get_chunks(session, doc_id, limit=min(limit, 500))


@router.post("/{doc_id}/digest", status_code=202)
async def regenerate_digest(doc_id: int, background_tasks: BackgroundTasks) -> dict:
    """（重新）產生導讀；既有文獻補導讀用。"""
    async with SessionLocal() as session:
        doc = await repo.get_document(session, doc_id)
    if doc is None:
        raise NotFoundError("document", doc_id)
    if doc["status"] != "ready":
        raise AppError("not_ready", "文獻尚未處理完成")
    background_tasks.add_task(generate_digest, doc_id)
    return {"status": "digesting"}


@router.delete("/{doc_id}", status_code=204)
async def delete_document(doc_id: int) -> None:
    async with SessionLocal() as session:
        file_path = await repo.delete_document(session, doc_id)
    if file_path is None:
        raise NotFoundError("document", doc_id)
    await AsyncPath(file_path).unlink(missing_ok=True)
