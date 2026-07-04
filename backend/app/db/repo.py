"""資料存取層：routers/services 不直接寫 SQL。"""

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_USER_ID = 1


def _row_to_dict(row: Any) -> dict:
    return dict(row._mapping)


# ---------- documents ----------

async def create_document(session: AsyncSession, filename: str, file_path: str) -> dict:
    row = (
        await session.execute(
            text(
                """
                INSERT INTO documents (user_id, filename, file_path)
                VALUES (:user_id, :filename, :file_path)
                RETURNING id, title, filename, page_count, status, error_msg, created_at
                """
            ),
            {"user_id": DEFAULT_USER_ID, "filename": filename, "file_path": file_path},
        )
    ).one()
    await session.commit()
    return _row_to_dict(row)


async def list_documents(session: AsyncSession) -> list[dict]:
    rows = await session.execute(
        text(
            """
            SELECT id, title, filename, page_count, status, error_msg, created_at
            FROM documents WHERE user_id = :user_id ORDER BY created_at DESC
            """
        ),
        {"user_id": DEFAULT_USER_ID},
    )
    return [_row_to_dict(r) for r in rows]


async def get_document(session: AsyncSession, doc_id: int) -> dict | None:
    row = (
        await session.execute(
            text(
                """
                SELECT id, title, filename, file_path, page_count, status,
                       error_msg, digest, created_at
                FROM documents WHERE id = :id AND user_id = :user_id
                """
            ),
            {"id": doc_id, "user_id": DEFAULT_USER_ID},
        )
    ).one_or_none()
    return _row_to_dict(row) if row else None


async def set_document_status(
    session: AsyncSession, doc_id: int, status: str, error_msg: str | None = None
) -> None:
    await session.execute(
        text("UPDATE documents SET status = :status, error_msg = :error_msg WHERE id = :id"),
        {"id": doc_id, "status": status, "error_msg": error_msg},
    )
    await session.commit()


async def set_document_parsed(
    session: AsyncSession, doc_id: int, title: str, page_count: int
) -> None:
    await session.execute(
        text("UPDATE documents SET title = :title, page_count = :page_count WHERE id = :id"),
        {"id": doc_id, "title": title, "page_count": page_count},
    )
    await session.commit()


async def delete_document(session: AsyncSession, doc_id: int) -> str | None:
    """刪除並回傳 file_path（讓 caller 清檔案）；不存在回 None。"""
    row = (
        await session.execute(
            text(
                "DELETE FROM documents WHERE id = :id AND user_id = :user_id RETURNING file_path"
            ),
            {"id": doc_id, "user_id": DEFAULT_USER_ID},
        )
    ).one_or_none()
    await session.commit()
    return row.file_path if row else None


# ---------- chunks ----------

async def insert_chunks(session: AsyncSession, doc_id: int, chunks: list[dict]) -> list[int]:
    ids: list[int] = []
    for c in chunks:
        row = (
            await session.execute(
                text(
                    """
                    INSERT INTO chunks (document_id, chunk_index, page, section, content, bbox_list)
                    VALUES (:document_id, :chunk_index, :page, :section, :content, :bbox_list)
                    RETURNING id
                    """
                ),
                {
                    "document_id": doc_id,
                    "chunk_index": c["chunk_index"],
                    "page": c["page"],
                    "section": c.get("section"),
                    "content": c["content"],
                    "bbox_list": json.dumps(c["bbox_list"]),
                },
            )
        ).one()
        ids.append(row.id)
    await session.commit()
    return ids


async def update_chunk_embeddings(
    session: AsyncSession, chunk_ids: list[int], embeddings: list[list[float]]
) -> None:
    for chunk_id, emb in zip(chunk_ids, embeddings, strict=True):
        await session.execute(
            text("UPDATE chunks SET embedding = CAST(:emb AS vector) WHERE id = :id"),
            {"id": chunk_id, "emb": json.dumps(emb)},
        )
    await session.commit()


async def get_chunks(session: AsyncSession, doc_id: int, limit: int = 500) -> list[dict]:
    rows = await session.execute(
        text(
            """
            SELECT id, chunk_index, page, section, content, bbox_list
            FROM chunks WHERE document_id = :doc_id
            ORDER BY chunk_index LIMIT :limit
            """
        ),
        {"doc_id": doc_id, "limit": limit},
    )
    return [_row_to_dict(r) for r in rows]
