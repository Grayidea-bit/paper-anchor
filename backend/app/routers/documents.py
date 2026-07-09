import uuid
from pathlib import Path

import aiofiles
from anyio import Path as AsyncPath
from fastapi import APIRouter, BackgroundTasks, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.config import get_settings
from app.db import repo
from app.db.session import SessionLocal
from app.errors import AppError, NotFoundError
from app.services import backup
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
    try:
        async with aiofiles.open(str(dest), "wb") as f:
            while chunk := await file.read(_WRITE_CHUNK):
                written += len(chunk)
                if written > max_bytes:
                    msg = f"檔案超過 {settings.max_upload_mb}MB 上限"
                    raise AppError("file_too_large", msg)
                await f.write(chunk)
        if written == 0:
            raise AppError("empty_file", "檔案是空的")
    except AppError:
        await dest.unlink(missing_ok=True)
        raise

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


class DocumentPatch(BaseModel):
    project_id: int | None = None


@router.patch("/{doc_id}")
async def patch_document(doc_id: int, body: DocumentPatch) -> dict:
    """指派/移出專案（project_id=null → 未分類）。"""
    async with SessionLocal() as session:
        if body.project_id is not None and await repo.get_project(session, body.project_id) is None:
            raise NotFoundError("project", body.project_id)
        if not await repo.set_document_project(session, doc_id, body.project_id):
            raise NotFoundError("document", doc_id)
        doc = await repo.get_document(session, doc_id)
    doc.pop("file_path", None)
    return doc


@router.post("/{doc_id}/digest", status_code=202)
async def regenerate_digest(
    doc_id: int, background_tasks: BackgroundTasks, language: str | None = None
) -> dict:
    """（重新）產生導讀；既有文獻補導讀或切換導讀語言用。"""
    async with SessionLocal() as session:
        doc = await repo.get_document(session, doc_id)
    if doc is None:
        raise NotFoundError("document", doc_id)
    if doc["status"] != "ready":
        raise AppError("not_ready", "文獻尚未處理完成")
    background_tasks.add_task(generate_digest, doc_id, language)
    return {"status": "digesting"}


@router.post("/{doc_id}/reingest", status_code=202)
async def reingest_document(doc_id: int, background_tasks: BackgroundTasks) -> dict:
    """重新解析文獻（M15 T-FD-01 / D4）：清舊 chunks 重跑 ingest_document。

    文獻不存在 → 404。該文獻已在 ingest 中（status parsing/embedding，ingest 無全域鎖，
    靠文獻 status 判斷）或全域 backup/restore 進行中 → 409 `operation_running`（避免與
    還原互踩）。否則把 status 重置為 parsing（順帶清掉舊 error_msg）並排入背景 ingest。
    """
    async with SessionLocal() as session:
        doc = await repo.get_document(session, doc_id)
        if doc is None:
            raise NotFoundError("document", doc_id)
        if doc["status"] in ("parsing", "embedding") or backup.is_running():
            raise AppError("operation_running", "已有處理中的操作，請稍後再試", status=409)
        await repo.set_document_status(session, doc_id, "parsing")
        doc = await repo.get_document(session, doc_id)
    doc.pop("file_path", None)
    background_tasks.add_task(ingest_document, doc_id)
    return doc


@router.delete("/{doc_id}", status_code=204)
async def delete_document(doc_id: int) -> None:
    async with SessionLocal() as session:
        file_path = await repo.delete_document(session, doc_id)
    if file_path is None:
        raise NotFoundError("document", doc_id)
    await AsyncPath(file_path).unlink(missing_ok=True)
