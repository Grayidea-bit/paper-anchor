"""翻譯表（glossary）CRUD 路由（T-TR-01）。"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.db import repo
from app.db.session import SessionLocal
from app.errors import NotFoundError
from app.services import glossary as glossary_service

router = APIRouter(prefix="/api", tags=["glossary"])


class GlossaryCreate(BaseModel):
    """建立翻譯表條目請求。"""

    term: str = Field(max_length=200)
    page: int = Field(ge=1)
    bbox_list: list[tuple[float, float, float, float]] = Field(min_length=1)
    chunk_id: int | None = None


@router.get("/documents/{document_id}/glossary")
async def list_glossary(document_id: int) -> list[dict]:
    """列出某文獻的翻譯表條目。"""
    async with SessionLocal() as session:
        doc = await repo.get_document(session, document_id)
    if doc is None:
        raise NotFoundError("document", document_id)
    async with SessionLocal() as session:
        return await repo.list_glossary_entries(session, document_id)


@router.post("/documents/{document_id}/glossary", status_code=201)
async def create_glossary_entry(document_id: int, body: GlossaryCreate) -> dict:
    """建立翻譯表條目：呼叫 LLM 翻譯（失敗降級為空字串，條目仍建立）。"""
    async with SessionLocal() as session:
        doc = await repo.get_document(session, document_id)
    if doc is None:
        raise NotFoundError("document", document_id)
    async with SessionLocal() as session:
        return await glossary_service.create_entry(
            session,
            document_id,
            term=body.term,
            page=body.page,
            bbox_list=body.bbox_list,
            chunk_id=body.chunk_id,
        )


@router.post("/glossary/{entry_id}/retranslate")
async def retranslate_glossary_entry(entry_id: int) -> dict:
    """重打一次翻譯並更新該條目。"""
    async with SessionLocal() as session:
        result = await glossary_service.retranslate(session, entry_id)
    if result is None:
        raise NotFoundError("glossary_entry", entry_id)
    return result


@router.delete("/glossary/{entry_id}", status_code=204)
async def delete_glossary_entry(entry_id: int) -> None:
    """刪除翻譯表條目。"""
    async with SessionLocal() as session:
        if not await repo.delete_glossary_entry(session, entry_id):
            raise NotFoundError("glossary_entry", entry_id)
