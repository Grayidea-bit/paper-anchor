"""使用者標註 CRUD 路由（T-AN-01）。"""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.db import repo
from app.db.session import SessionLocal
from app.errors import NotFoundError

router = APIRouter(prefix="/api", tags=["annotations"])


class AnnotationCreate(BaseModel):
    """建立標註請求。"""

    type: Literal["underline", "highlight", "note"]
    color: Literal["amber", "terracotta", "sage", "slate"] = "amber"
    page: int = Field(ge=1)
    bbox_list: list[tuple[float, float, float, float]] = Field(min_length=1)
    chunk_id: int | None = None
    selected_text: str = Field(default="", max_length=3000)
    note_text: str = Field(default="", max_length=2000)


class AnnotationUpdate(BaseModel):
    """更新標註請求（部分更新）。"""

    note_text: str | None = Field(default=None, max_length=2000)
    color: Literal["amber", "terracotta", "sage", "slate"] | None = None


@router.get("/documents/{document_id}/annotations")
async def list_annotations(document_id: int) -> list[dict]:
    """列出某文獻的所有標註。"""
    async with SessionLocal() as session:
        # 驗證文獻存在
        doc = await repo.get_document(session, document_id)
    if doc is None:
        raise NotFoundError("document", document_id)
    async with SessionLocal() as session:
        return await repo.list_annotations(session, document_id)


@router.post("/documents/{document_id}/annotations", status_code=201)
async def create_annotation(document_id: int, body: AnnotationCreate) -> dict:
    """建立新標註。"""
    async with SessionLocal() as session:
        # 驗證文獻存在
        doc = await repo.get_document(session, document_id)
    if doc is None:
        raise NotFoundError("document", document_id)
    async with SessionLocal() as session:
        return await repo.create_annotation(
            session,
            document_id,
            type=body.type,
            color=body.color,
            page=body.page,
            bbox_list=body.bbox_list,
            chunk_id=body.chunk_id,
            selected_text=body.selected_text,
            note_text=body.note_text,
        )


@router.patch("/annotations/{annotation_id}")
async def update_annotation(annotation_id: int, body: AnnotationUpdate) -> dict:
    """部分更新標註（note_text 與 color）。"""
    async with SessionLocal() as session:
        result = await repo.update_annotation(
            session,
            annotation_id,
            note_text=body.note_text,
            color=body.color,
        )
    if result is None:
        raise NotFoundError("annotation", annotation_id)
    return result


@router.delete("/annotations/{annotation_id}", status_code=204)
async def delete_annotation(annotation_id: int) -> None:
    """刪除標註。"""
    async with SessionLocal() as session:
        if not await repo.delete_annotation(session, annotation_id):
            raise NotFoundError("annotation", annotation_id)
