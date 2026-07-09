"""資料存取層：routers/services 不直接寫 SQL。"""

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_USER_ID = 1


def _row_to_dict(row: Any) -> dict:
    return dict(row._mapping)


# ---------- projects ----------


async def create_project(session: AsyncSession, name: str) -> dict:
    row = (
        await session.execute(
            text(
                """
                INSERT INTO projects (user_id, name) VALUES (:user_id, :name)
                RETURNING id, name, created_at
                """
            ),
            {"user_id": DEFAULT_USER_ID, "name": name},
        )
    ).one()
    await session.commit()
    return _row_to_dict(row)


async def list_projects(session: AsyncSession) -> list[dict]:
    rows = await session.execute(
        text(
            """
            SELECT p.id, p.name, p.created_at,
                   COUNT(d.id) AS document_count
            FROM projects p
            LEFT JOIN documents d ON d.project_id = p.id
            WHERE p.user_id = :user_id
            GROUP BY p.id ORDER BY p.created_at
            """
        ),
        {"user_id": DEFAULT_USER_ID},
    )
    return [_row_to_dict(r) for r in rows]


async def get_project(session: AsyncSession, project_id: int) -> dict | None:
    row = (
        await session.execute(
            text("SELECT id, name, created_at FROM projects WHERE id = :id AND user_id = :uid"),
            {"id": project_id, "uid": DEFAULT_USER_ID},
        )
    ).one_or_none()
    return _row_to_dict(row) if row else None


async def rename_project(session: AsyncSession, project_id: int, name: str) -> dict | None:
    row = (
        await session.execute(
            text(
                """
                UPDATE projects SET name = :name
                WHERE id = :id AND user_id = :uid
                RETURNING id, name, created_at
                """
            ),
            {"id": project_id, "name": name, "uid": DEFAULT_USER_ID},
        )
    ).one_or_none()
    await session.commit()
    return _row_to_dict(row) if row else None


async def delete_project(session: AsyncSession, project_id: int) -> bool:
    """刪除專案；文獻回未分類（FK SET NULL）、專案對話級聯刪除（FK CASCADE）。"""
    row = (
        await session.execute(
            text("DELETE FROM projects WHERE id = :id AND user_id = :uid RETURNING id"),
            {"id": project_id, "uid": DEFAULT_USER_ID},
        )
    ).one_or_none()
    await session.commit()
    return row is not None


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
            SELECT id, project_id, title, filename, page_count, status, error_msg, created_at
            FROM documents WHERE user_id = :user_id ORDER BY created_at DESC
            """
        ),
        {"user_id": DEFAULT_USER_ID},
    )
    return [_row_to_dict(r) for r in rows]


async def set_document_project(session: AsyncSession, doc_id: int, project_id: int | None) -> bool:
    row = (
        await session.execute(
            text(
                """
                UPDATE documents SET project_id = :pid
                WHERE id = :id AND user_id = :uid RETURNING id
                """
            ),
            {"id": doc_id, "pid": project_id, "uid": DEFAULT_USER_ID},
        )
    ).one_or_none()
    await session.commit()
    return row is not None


async def get_document(session: AsyncSession, doc_id: int) -> dict | None:
    row = (
        await session.execute(
            text(
                """
                SELECT id, project_id, title, filename, file_path, page_count, status,
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


async def reconcile_interrupted_ingests(session: AsyncSession) -> int:
    """啟動時自癒（M15 T-FD-01 / D4）：把卡在 transient ingest 狀態的文獻轉 failed。

    程序被殺（重啟／OOM／`--reload`）會讓 ingest 中途文獻永久卡在 parsing/embedding
    ——這類非終態前端會永遠顯示處理中且無重試入口。lifespan 啟動時視為「上一輪被中斷」，
    一律重置為 failed（帶可讀 error_msg），使其可經 reingest 端點救回。回傳受影響筆數。
    """
    result = await session.execute(
        text(
            """
            UPDATE documents SET status = 'failed',
                error_msg = '處理中斷（伺服器重啟），請重新解析'
            WHERE status IN ('parsing', 'embedding')
            """
        )
    )
    await session.commit()
    return result.rowcount or 0


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
            text("DELETE FROM documents WHERE id = :id AND user_id = :user_id RETURNING file_path"),
            {"id": doc_id, "user_id": DEFAULT_USER_ID},
        )
    ).one_or_none()
    await session.commit()
    return row.file_path if row else None


# ---------- chunks ----------

# 一篇論文常見 300+ chunk；單條多列 INSERT 分批送出，防止單條 SQL 的綁定參數數量
# 爆掉（Postgres 上限 65535 個；此處保守抓 500 列 * 6 欄 = 3000 個，兩邊 DB 都安全）。
_INSERT_CHUNKS_BATCH_SIZE = 500


async def insert_chunks(session: AsyncSession, doc_id: int, chunks: list[dict]) -> list[int]:
    """批次寫入 chunks（單條多列 INSERT ... VALUES ... RETURNING），取代逐列 INSERT。

    一篇 300 chunk 的論文逐列 INSERT 是 300 次 round-trip；改多列 VALUES 一次送出，
    大批次時每 `_INSERT_CHUNKS_BATCH_SIZE` 列分批（防參數上限）。RETURNING 順序在
    Postgres 多列 VALUES 下實務對齊輸入序，但這裡不依賴該實作細節——改用 RETURNING
    id, chunk_index 後在 Python 端依 chunk_index（UNIQUE(document_id, chunk_index)
    保證唯一）重建對應輸入序的 id 清單，兩邊 DB 皆穩妥。空清單短路。
    """
    if not chunks:
        return []

    ids_by_chunk_index: dict[int, int] = {}
    for batch_start in range(0, len(chunks), _INSERT_CHUNKS_BATCH_SIZE):
        batch = chunks[batch_start : batch_start + _INSERT_CHUNKS_BATCH_SIZE]
        values_clauses: list[str] = []
        params: dict[str, Any] = {}
        for i, c in enumerate(batch):
            values_clauses.append(
                f"(:document_id_{i}, :chunk_index_{i}, :page_{i}, :section_{i}, "
                f":content_{i}, :bbox_list_{i})"
            )
            params[f"document_id_{i}"] = doc_id
            params[f"chunk_index_{i}"] = c["chunk_index"]
            params[f"page_{i}"] = c["page"]
            params[f"section_{i}"] = c.get("section")
            params[f"content_{i}"] = c["content"]
            params[f"bbox_list_{i}"] = json.dumps(c["bbox_list"])

        stmt = text(
            f"""
            INSERT INTO chunks (document_id, chunk_index, page, section, content, bbox_list)
            VALUES {", ".join(values_clauses)}
            RETURNING id, chunk_index
            """
        )
        rows = (await session.execute(stmt, params)).all()
        for row in rows:
            ids_by_chunk_index[row.chunk_index] = row.id

    await session.commit()
    return [ids_by_chunk_index[c["chunk_index"]] for c in chunks]


async def update_chunk_embeddings(
    session: AsyncSession, chunk_ids: list[int], embeddings: list[list[float]]
) -> None:
    """批次更新 chunk 向量：單條 UPDATE 語句 + executemany 參數清單（取代逐筆 UPDATE）。

    `session.execute(stmt, [params, ...])`（list of dict）觸發 DBAPI 層 executemany，
    SQLite 與 asyncpg 皆相容；asyncpg 會將整批 bind/execute 用一次協定往返送出，
    把 300 次 round-trip 收斂成 1 次。筆數不符時沿用既有 zip(strict=True) 守護。
    """
    if not chunk_ids:
        return
    params = [
        {"id": chunk_id, "emb": json.dumps(emb)}
        for chunk_id, emb in zip(chunk_ids, embeddings, strict=True)
    ]
    await session.execute(
        text("UPDATE chunks SET embedding = CAST(:emb AS vector) WHERE id = :id"),
        params,
    )
    await session.commit()


async def update_document_digest(
    session: AsyncSession, doc_id: int, digest: dict, usage: dict
) -> None:
    await session.execute(
        text(
            """
            UPDATE documents
            SET digest = CAST(:digest AS jsonb),
                token_usage = token_usage || CAST(:usage AS jsonb)
            WHERE id = :id
            """
        ),
        {"id": doc_id, "digest": json.dumps(digest), "usage": json.dumps({"digest": usage})},
    )
    await session.commit()


async def similar_chunks_scoped(
    session: AsyncSession,
    embedding: list[float],
    k: int,
    *,
    doc_id: int | None = None,
    project_id: int | None = None,
) -> list[dict]:
    """向量檢索，範圍隔離在 SQL 層（docs/02 D6）：
    doc_id → 單篇；project_id → 該專案全部文獻；皆 None → 全庫。
    多文獻時每篇最多 4 條（window function 防單篇洗版）。
    """
    params: dict = {"emb": json.dumps(embedding), "k": k, "uid": DEFAULT_USER_ID}
    if doc_id is not None:
        rows = await session.execute(
            text(
                """
                SELECT c.id, c.document_id, c.chunk_index, c.page, c.section,
                       c.content, c.bbox_list, d.title AS document_title
                FROM chunks c
                JOIN documents d ON d.id = c.document_id AND d.user_id = :uid
                WHERE c.document_id = :doc_id AND c.embedding IS NOT NULL
                ORDER BY c.embedding <=> CAST(:emb AS vector)
                LIMIT :k
                """
            ),
            {**params, "doc_id": doc_id},
        )
        return [_row_to_dict(r) for r in rows]

    project_filter = "AND d.project_id = :pid" if project_id is not None else ""
    if project_id is not None:
        params["pid"] = project_id
    rows = await session.execute(
        text(
            f"""
            SELECT id, document_id, chunk_index, page, section,
                   content, bbox_list, document_title
            FROM (
                SELECT c.id, c.document_id, c.chunk_index, c.page, c.section,
                       c.content, c.bbox_list, d.title AS document_title,
                       c.embedding <=> CAST(:emb AS vector) AS dist,
                       ROW_NUMBER() OVER (
                           PARTITION BY c.document_id
                           ORDER BY c.embedding <=> CAST(:emb AS vector)
                       ) AS rank_in_doc
                FROM chunks c
                JOIN documents d ON d.id = c.document_id
                     AND d.user_id = :uid AND d.status = 'ready' {project_filter}
                WHERE c.embedding IS NOT NULL
            ) ranked
            WHERE rank_in_doc <= 4
            ORDER BY dist
            LIMIT :k
            """
        ),
        params,
    )
    return [_row_to_dict(r) for r in rows]


async def chunks_by_indexes(session: AsyncSession, doc_id: int, indexes: list[int]) -> list[dict]:
    if not indexes:
        return []
    stmt = text(
        """
        SELECT c.id, c.document_id, c.chunk_index, c.page, c.section,
               c.content, c.bbox_list, d.title AS document_title
        FROM chunks c JOIN documents d ON d.id = c.document_id
        WHERE c.document_id = :doc_id AND c.chunk_index IN :indexes
        ORDER BY c.chunk_index
        """
    ).bindparams(bindparam("indexes", expanding=True))
    rows = await session.execute(stmt, {"doc_id": doc_id, "indexes": indexes})
    return [_row_to_dict(r) for r in rows]


async def chunks_by_ids(session: AsyncSession, doc_id: int, ids: list[int]) -> list[dict]:
    if not ids:
        return []
    stmt = text(
        """
        SELECT c.id, c.document_id, c.chunk_index, c.page, c.section,
               c.content, c.bbox_list, d.title AS document_title
        FROM chunks c JOIN documents d ON d.id = c.document_id
        WHERE c.document_id = :doc_id AND c.id IN :ids
        ORDER BY c.chunk_index
        """
    ).bindparams(bindparam("ids", expanding=True))
    rows = await session.execute(stmt, {"doc_id": doc_id, "ids": ids})
    return [_row_to_dict(r) for r in rows]


async def total_token_usage(session: AsyncSession) -> dict:
    """全站累計 token（messages 逐則 + documents 導讀）。"""
    row = (
        await session.execute(
            text(
                """
                SELECT
                  COALESCE((SELECT SUM((token_usage->>'prompt_tokens')::bigint)
                            FROM messages WHERE token_usage->>'prompt_tokens' IS NOT NULL), 0)
                + COALESCE((SELECT SUM((token_usage#>>'{digest,prompt_tokens}')::bigint)
                            FROM documents
                            WHERE token_usage #>> '{digest,prompt_tokens}' IS NOT NULL), 0)
                  AS prompt_tokens,
                  COALESCE((SELECT SUM((token_usage->>'completion_tokens')::bigint)
                            FROM messages WHERE token_usage->>'completion_tokens' IS NOT NULL), 0)
                + COALESCE((SELECT SUM((token_usage#>>'{digest,completion_tokens}')::bigint)
                            FROM documents
                            WHERE token_usage #>> '{digest,completion_tokens}' IS NOT NULL), 0)
                  AS completion_tokens
                """
            )
        )
    ).one()
    return {
        "prompt_tokens": int(row.prompt_tokens),
        "completion_tokens": int(row.completion_tokens),
    }


def escape_like(text_value: str) -> str:
    """跳脫 ILIKE 的萬用字元（% _ \\），供關鍵字工具使用。"""
    return text_value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def search_chunks_scoped(
    session: AsyncSession,
    query: str,
    k: int,
    *,
    doc_id: int | None = None,
    project_id: int | None = None,
) -> list[dict]:
    """關鍵字全文檢索（ILIKE），範圍隔離同 similar_chunks_scoped（SQL 層）。"""
    pattern = f"%{escape_like(query)}%"
    filters = ["d.user_id = :uid", "c.content ILIKE :pat ESCAPE '\\'"]
    params: dict = {"uid": DEFAULT_USER_ID, "pat": pattern, "k": k}
    if doc_id is not None:
        filters.append("c.document_id = :doc_id")
        params["doc_id"] = doc_id
    elif project_id is not None:
        filters.append("d.project_id = :pid")
        params["pid"] = project_id
        filters.append("d.status = 'ready'")
    else:
        filters.append("d.status = 'ready'")
    rows = await session.execute(
        text(
            f"""
            SELECT c.id, c.document_id, c.chunk_index, c.page, c.section,
                   c.content, c.bbox_list, d.title AS document_title
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE {" AND ".join(filters)}
            ORDER BY c.document_id, c.chunk_index
            LIMIT :k
            """
        ),
        params,
    )
    return [_row_to_dict(r) for r in rows]


# ---------- conversations / messages ----------


async def create_conversation(
    session: AsyncSession,
    *,
    scope: str,
    title: str,
    document_id: int | None = None,
    project_id: int | None = None,
) -> dict:
    row = (
        await session.execute(
            text(
                """
                INSERT INTO conversations (scope, document_id, project_id, title)
                VALUES (:scope, :doc_id, :pid, :title)
                RETURNING id, scope, document_id, project_id, title, model, created_at
                """
            ),
            {"scope": scope, "doc_id": document_id, "pid": project_id, "title": title},
        )
    ).one()
    await session.commit()
    return _row_to_dict(row)


async def list_conversations_scoped(
    session: AsyncSession,
    *,
    scope: str,
    document_id: int | None = None,
    project_id: int | None = None,
) -> list[dict]:
    rows = await session.execute(
        text(
            """
            SELECT id, scope, document_id, project_id, title, model, created_at
            FROM conversations
            WHERE scope = :scope
              AND (CAST(:doc_id AS bigint) IS NULL OR document_id = :doc_id)
              AND (CAST(:pid AS bigint) IS NULL OR project_id = :pid)
            ORDER BY created_at DESC
            """
        ),
        {"scope": scope, "doc_id": document_id, "pid": project_id},
    )
    return [_row_to_dict(r) for r in rows]


async def get_conversation(session: AsyncSession, conv_id: int) -> dict | None:
    row = (
        await session.execute(
            text(
                """
                SELECT id, scope, document_id, project_id, title, model, created_at
                FROM conversations WHERE id = :id
                """
            ),
            {"id": conv_id},
        )
    ).one_or_none()
    return _row_to_dict(row) if row else None


async def set_conversation_model(session: AsyncSession, conv_id: int, model: str | None) -> None:
    """空字串或 None 存 NULL（回落來源預設）。"""
    await session.execute(
        text("UPDATE conversations SET model = :m WHERE id = :id"),
        {"id": conv_id, "m": model or None},
    )
    await session.commit()


async def add_message(
    session: AsyncSession,
    conv_id: int,
    role: str,
    content: str,
    citations: list | None = None,
    selection: dict | None = None,
    token_usage: dict | None = None,
) -> dict:
    row = (
        await session.execute(
            text(
                """
                INSERT INTO messages
                    (conversation_id, role, content, citations, selection, token_usage)
                VALUES (:conv_id, :role, :content, CAST(:citations AS jsonb),
                        CAST(:selection AS jsonb), CAST(:token_usage AS jsonb))
                RETURNING id, role, content, citations, selection, token_usage, created_at
                """
            ),
            {
                "conv_id": conv_id,
                "role": role,
                "content": content,
                "citations": json.dumps(citations or []),
                "selection": json.dumps(selection) if selection else None,
                "token_usage": json.dumps(token_usage or {}),
            },
        )
    ).one()
    await session.commit()
    return _row_to_dict(row)


async def list_messages(session: AsyncSession, conv_id: int) -> list[dict]:
    rows = await session.execute(
        text(
            """
            SELECT id, role, content, citations, selection, token_usage, created_at
            FROM messages WHERE conversation_id = :conv_id ORDER BY id
            """
        ),
        {"conv_id": conv_id},
    )
    return [_row_to_dict(r) for r in rows]


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


# ---------- backup 匯出（M12 D10 / T-BK-02）----------

# 白名單表 → 明確欄位清單（不用 SELECT *）。
# documents 不含 embedding 類欄位；chunks 整表不匯出（可由 PDF 重建，見 D10）。
_DUMP_TABLE_COLUMNS: dict[str, list[str]] = {
    "documents": [
        "id",
        "user_id",
        "project_id",
        "title",
        "filename",
        "file_path",
        "page_count",
        "status",
        "error_msg",
        "digest",
        "token_usage",
        "created_at",
    ],
    "projects": ["id", "user_id", "name", "created_at"],
    "annotations": [
        "id",
        "document_id",
        "type",
        "color",
        "page",
        "bbox_list",
        "chunk_id",
        "selected_text",
        "note_text",
        "created_at",
        "updated_at",
    ],
    "glossary_entries": [
        "id",
        "document_id",
        "term",
        "translation",
        "target_lang",
        "page",
        "bbox_list",
        "chunk_id",
        "notes",
        "created_at",
    ],
    "conversations": ["id", "scope", "document_id", "project_id", "title", "model", "created_at"],
    "messages": [
        "id",
        "conversation_id",
        "role",
        "content",
        "citations",
        "selection",
        "token_usage",
        "created_at",
    ],
}


def _normalize_dump_row(row: dict) -> dict:
    """datetime → isoformat 字串；JSONB 欄位原樣保留（型別由 DB 驅動層決定）。"""
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in row.items()}


async def dump_table_rows(session: AsyncSession, table: str) -> list[dict]:
    """匯出白名單表的全部列（備份匯出用，M12 T-BK-02）。

    只允許 `_DUMP_TABLE_COLUMNS` 內的表與其明確欄位清單；其餘表（含 chunks）一律拒絕，
    以避免有人不小心把 embedding 或未來新表夾帶進備份。
    """
    columns = _DUMP_TABLE_COLUMNS.get(table)
    if columns is None:
        raise ValueError(f"table not allowed for dump: {table}")
    column_list = ", ".join(columns)
    rows = await session.execute(text(f"SELECT {column_list} FROM {table} ORDER BY id"))
    return [_normalize_dump_row(_row_to_dict(r)) for r in rows]


# ---------- annotations ----------


async def create_annotation(
    session: AsyncSession,
    document_id: int,
    *,
    type: str,
    color: str,
    page: int,
    bbox_list: list,
    chunk_id: int | None = None,
    selected_text: str = "",
    note_text: str = "",
) -> dict:
    """建立標註。"""
    row = (
        await session.execute(
            text(
                """
                INSERT INTO annotations
                    (document_id, type, color, page, bbox_list, chunk_id, selected_text, note_text)
                VALUES (:document_id, :type, :color, :page, CAST(:bbox_list AS jsonb),
                        :chunk_id, :selected_text, :note_text)
                RETURNING id, document_id, type, color, page, bbox_list, chunk_id,
                          selected_text, note_text, created_at, updated_at
                """
            ),
            {
                "document_id": document_id,
                "type": type,
                "color": color,
                "page": page,
                "bbox_list": json.dumps(bbox_list),
                "chunk_id": chunk_id,
                "selected_text": selected_text,
                "note_text": note_text,
            },
        )
    ).one()
    await session.commit()
    return _row_to_dict(row)


async def list_annotations(session: AsyncSession, document_id: int) -> list[dict]:
    """列出某文獻的所有標註，按頁碼與建立時間排序。"""
    rows = await session.execute(
        text(
            """
            SELECT id, document_id, type, color, page, bbox_list, chunk_id,
                   selected_text, note_text, created_at, updated_at
            FROM annotations
            WHERE document_id = :document_id
            ORDER BY page, created_at
            """
        ),
        {"document_id": document_id},
    )
    return [_row_to_dict(r) for r in rows]


async def update_annotation(
    session: AsyncSession,
    annotation_id: int,
    *,
    note_text: str | None = None,
    color: str | None = None,
) -> dict | None:
    """部分更新標註（note_text 與 color），touch updated_at；找不到回 None。"""
    updates = []
    params: dict = {"id": annotation_id}
    if note_text is not None:
        updates.append("note_text = :note_text")
        params["note_text"] = note_text
    if color is not None:
        updates.append("color = :color")
        params["color"] = color
    if not updates:
        # 沒有更新欄位，直接讀回原資料
        row = (
            await session.execute(
                text(
                    """
                    SELECT id, document_id, type, color, page, bbox_list, chunk_id,
                           selected_text, note_text, created_at, updated_at
                    FROM annotations WHERE id = :id
                    """
                ),
                params,
            )
        ).one_or_none()
        return _row_to_dict(row) if row else None
    updates.append("updated_at = CURRENT_TIMESTAMP")
    update_clause = ", ".join(updates)
    row = (
        await session.execute(
            text(
                f"""
                UPDATE annotations
                SET {update_clause}
                WHERE id = :id
                RETURNING id, document_id, type, color, page, bbox_list, chunk_id,
                          selected_text, note_text, created_at, updated_at
                """
            ),
            params,
        )
    ).one_or_none()
    await session.commit()
    return _row_to_dict(row) if row else None


async def delete_annotation(session: AsyncSession, annotation_id: int) -> bool:
    """刪除標註；不存在回 False。"""
    row = (
        await session.execute(
            text("DELETE FROM annotations WHERE id = :id RETURNING id"),
            {"id": annotation_id},
        )
    ).one_or_none()
    await session.commit()
    return row is not None


async def create_glossary_entry(
    session: AsyncSession,
    document_id: int,
    *,
    term: str,
    translation: str,
    target_lang: str,
    page: int,
    bbox_list: list,
    chunk_id: int | None = None,
    notes: str = "",
) -> dict:
    """建立翻譯表條目。"""
    row = (
        await session.execute(
            text(
                """
                INSERT INTO glossary_entries
                    (document_id, term, translation, target_lang, page, bbox_list, chunk_id, notes)
                VALUES (:document_id, :term, :translation, :target_lang, :page,
                        CAST(:bbox_list AS jsonb), :chunk_id, :notes)
                RETURNING id, document_id, term, translation, target_lang, page, bbox_list,
                          chunk_id, notes, created_at
                """
            ),
            {
                "document_id": document_id,
                "term": term,
                "translation": translation,
                "target_lang": target_lang,
                "page": page,
                "bbox_list": json.dumps(bbox_list),
                "chunk_id": chunk_id,
                "notes": notes,
            },
        )
    ).one()
    await session.commit()
    return _row_to_dict(row)


async def list_glossary_entries(session: AsyncSession, document_id: int) -> list[dict]:
    """列出某文獻的翻譯表條目，按頁碼與建立時間排序。"""
    rows = await session.execute(
        text(
            """
            SELECT id, document_id, term, translation, target_lang, page, bbox_list,
                   chunk_id, notes, created_at
            FROM glossary_entries
            WHERE document_id = :document_id
            ORDER BY page, created_at
            """
        ),
        {"document_id": document_id},
    )
    return [_row_to_dict(r) for r in rows]


async def get_glossary_entry(session: AsyncSession, entry_id: int) -> dict | None:
    row = (
        await session.execute(
            text(
                """
                SELECT id, document_id, term, translation, target_lang, page, bbox_list,
                       chunk_id, notes, created_at
                FROM glossary_entries WHERE id = :id
                """
            ),
            {"id": entry_id},
        )
    ).one_or_none()
    return _row_to_dict(row) if row else None


async def update_glossary_translation(
    session: AsyncSession, entry_id: int, translation: str
) -> dict | None:
    """更新翻譯表條目的譯文（retranslate 用）；找不到回 None。"""
    row = (
        await session.execute(
            text(
                """
                UPDATE glossary_entries
                SET translation = :translation
                WHERE id = :id
                RETURNING id, document_id, term, translation, target_lang, page, bbox_list,
                          chunk_id, notes, created_at
                """
            ),
            {"id": entry_id, "translation": translation},
        )
    ).one_or_none()
    await session.commit()
    return _row_to_dict(row) if row else None


async def delete_glossary_entry(session: AsyncSession, entry_id: int) -> bool:
    """刪除翻譯表條目；不存在回 False。"""
    row = (
        await session.execute(
            text("DELETE FROM glossary_entries WHERE id = :id RETURNING id"),
            {"id": entry_id},
        )
    ).one_or_none()
    await session.commit()
    return row is not None


async def get_chunk(session: AsyncSession, chunk_id: int) -> dict | None:
    """取單一 chunk（翻譯上下文用）。"""
    row = (
        await session.execute(
            text(
                """
                SELECT id, document_id, chunk_index, page, section, content, bbox_list
                FROM chunks WHERE id = :id
                """
            ),
            {"id": chunk_id},
        )
    ).one_or_none()
    return _row_to_dict(row) if row else None


async def list_annotations_scoped(
    session: AsyncSession,
    *,
    document_id: int | None = None,
    project_id: int | None = None,
    type_filter: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """
    查詢標註，範圍隔離給 AI 工具用。
    - document_id 給定 → 該文獻標註
    - project_id 給定 → JOIN documents 過濾該專案
    - 兩者皆 None → 全庫標註
    - type_filter 給定 → 過濾 type
    """
    filters = ["d.user_id = :uid"]
    params: dict = {"uid": DEFAULT_USER_ID, "limit": limit}

    if document_id is not None:
        filters.append("a.document_id = :doc_id")
        params["doc_id"] = document_id
    elif project_id is not None:
        filters.append("d.project_id = :pid")
        params["pid"] = project_id

    if type_filter is not None:
        filters.append("a.type = :type_filter")
        params["type_filter"] = type_filter

    where_clause = " AND ".join(filters)
    rows = await session.execute(
        text(
            f"""
            SELECT a.id, a.document_id, a.type, a.color, a.page, a.bbox_list, a.chunk_id,
                   a.selected_text, a.note_text, a.created_at, a.updated_at,
                   d.title AS document_title
            FROM annotations a
            JOIN documents d ON d.id = a.document_id
            WHERE {where_clause}
            ORDER BY a.page, a.created_at
            LIMIT :limit
            """
        ),
        params,
    )
    return [_row_to_dict(r) for r in rows]


# ---------- restore 匯入（M13 D11 / T-RS-01）----------
# 還原專用寫入：全部支援顯式 created_at/updated_at（保留備份端時間戳，不用 DB 預設），
# 回傳新生 id 供關聯欄位 remap。annotations/glossary 的 chunk_id 一律 NULL——備份端舊
# chunk_id 是真 FK，本機 chunks 由 ingest 全新重生，沿用必 FK violation（見 D11）。
# 查詢面（判斷本地是否已存在對應列）複用既有 list_* / dump_table_rows，不另開函式。


def _coerce_ts(value: str | datetime) -> datetime:
    """時間戳參數統一 coerce 成 aware datetime（介面對呼叫端寬容，收 ISO 字串或 datetime）。

    asyncpg 對 TIMESTAMPTZ 參數只收 datetime 物件、拒收 ISO 字串（DataError；T-RS-03 真
    Postgres E2E 發現，SQLite 測試環境不會攔到）。naive 一律視為 UTC；容忍 `Z` 結尾與
    空格/`T` 分隔（`fromisoformat` 自 3.11 起兩者皆收）。
    """
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


async def restore_insert_project(
    session: AsyncSession, *, name: str, created_at: str | datetime
) -> int:
    """插入專案（顯式 created_at），回傳新 id。"""
    row = (
        await session.execute(
            text(
                """
                INSERT INTO projects (user_id, name, created_at)
                VALUES (:uid, :name, :created_at)
                RETURNING id
                """
            ),
            {"uid": DEFAULT_USER_ID, "name": name, "created_at": _coerce_ts(created_at)},
        )
    ).one()
    await session.commit()
    return int(row.id)


async def restore_insert_document(
    session: AsyncSession,
    *,
    project_id: int | None,
    title: str,
    filename: str,
    file_path: str,
    digest: dict | None,
    token_usage: dict | None,
    created_at: str | datetime,
) -> int:
    """插入還原文獻（status='uploaded'，待 ingest 推進），回傳新 id。

    digest/token_usage 沿用 dump 值（digest 有值時 ingest 可 run_digest=False 省 LLM）。
    """
    row = (
        await session.execute(
            text(
                """
                INSERT INTO documents
                    (user_id, project_id, title, filename, file_path, status,
                     digest, token_usage, created_at)
                VALUES (:uid, :pid, :title, :filename, :file_path, 'uploaded',
                        :digest, :token_usage, :created_at)
                RETURNING id
                """
            ),
            {
                "uid": DEFAULT_USER_ID,
                "pid": project_id,
                "title": title,
                "filename": filename,
                "file_path": file_path,
                "digest": json.dumps(digest) if digest is not None else None,
                "token_usage": json.dumps(token_usage or {}),
                "created_at": _coerce_ts(created_at),
            },
        )
    ).one()
    await session.commit()
    return int(row.id)


async def restore_update_document_digest(session: AsyncSession, doc_id: int, digest: dict) -> None:
    """還原時回寫 remap 後的 digest（citations document_id 修正，見 D11）。

    異於 `update_document_digest`：不追加 token_usage（dump 的 token_usage 已在
    `restore_insert_document` 原樣寫入，這裡只修 digest 內容）。
    """
    await session.execute(
        text("UPDATE documents SET digest = :digest WHERE id = :id"),
        {"id": doc_id, "digest": json.dumps(digest)},
    )
    await session.commit()


async def restore_insert_annotation(
    session: AsyncSession,
    *,
    document_id: int,
    type: str,
    color: str,
    page: int,
    bbox_list: list,
    selected_text: str,
    note_text: str,
    created_at: str | datetime,
    updated_at: str | datetime,
) -> int:
    """插入還原標註（顯式時間戳，chunk_id 一律 NULL），回傳新 id。"""
    row = (
        await session.execute(
            text(
                """
                INSERT INTO annotations
                    (document_id, type, color, page, bbox_list, chunk_id,
                     selected_text, note_text, created_at, updated_at)
                VALUES (:document_id, :type, :color, :page, :bbox_list, NULL,
                        :selected_text, :note_text, :created_at, :updated_at)
                RETURNING id
                """
            ),
            {
                "document_id": document_id,
                "type": type,
                "color": color,
                "page": page,
                "bbox_list": json.dumps(bbox_list),
                "selected_text": selected_text,
                "note_text": note_text,
                "created_at": _coerce_ts(created_at),
                "updated_at": _coerce_ts(updated_at),
            },
        )
    ).one()
    await session.commit()
    return int(row.id)


async def restore_overwrite_annotation(
    session: AsyncSession,
    annotation_id: int,
    *,
    note_text: str,
    color: str,
    selected_text: str,
    updated_at: str | datetime,
) -> None:
    """備份較新時覆蓋既有標註的可編輯欄位 + 顯式 updated_at（D11 newer-wins）。"""
    await session.execute(
        text(
            """
            UPDATE annotations
            SET note_text = :note_text, color = :color, selected_text = :selected_text,
                updated_at = :updated_at
            WHERE id = :id
            """
        ),
        {
            "id": annotation_id,
            "note_text": note_text,
            "color": color,
            "selected_text": selected_text,
            "updated_at": _coerce_ts(updated_at),
        },
    )
    await session.commit()


async def restore_insert_glossary_entry(
    session: AsyncSession,
    *,
    document_id: int,
    term: str,
    translation: str,
    target_lang: str,
    page: int,
    bbox_list: list,
    notes: str,
    created_at: str | datetime,
) -> int:
    """插入還原翻譯表條目（顯式 created_at，chunk_id 一律 NULL），回傳新 id。"""
    row = (
        await session.execute(
            text(
                """
                INSERT INTO glossary_entries
                    (document_id, term, translation, target_lang, page, bbox_list,
                     chunk_id, notes, created_at)
                VALUES (:document_id, :term, :translation, :target_lang, :page,
                        :bbox_list, NULL, :notes, :created_at)
                RETURNING id
                """
            ),
            {
                "document_id": document_id,
                "term": term,
                "translation": translation,
                "target_lang": target_lang,
                "page": page,
                "bbox_list": json.dumps(bbox_list),
                "notes": notes,
                "created_at": _coerce_ts(created_at),
            },
        )
    ).one()
    await session.commit()
    return int(row.id)


async def restore_insert_conversation(
    session: AsyncSession,
    *,
    scope: str,
    document_id: int | None,
    project_id: int | None,
    title: str,
    model: str | None,
    created_at: str | datetime,
) -> int:
    """插入還原對話串（保留 model 與顯式 created_at），回傳新 id。"""
    row = (
        await session.execute(
            text(
                """
                INSERT INTO conversations
                    (scope, document_id, project_id, title, model, created_at)
                VALUES (:scope, :document_id, :project_id, :title, :model, :created_at)
                RETURNING id
                """
            ),
            {
                "scope": scope,
                "document_id": document_id,
                "project_id": project_id,
                "title": title,
                "model": model,
                "created_at": _coerce_ts(created_at),
            },
        )
    ).one()
    await session.commit()
    return int(row.id)


async def restore_insert_message(
    session: AsyncSession,
    *,
    conversation_id: int,
    role: str,
    content: str,
    citations: list,
    selection: dict | None,
    token_usage: dict | None,
    created_at: str | datetime,
) -> int:
    """插入還原訊息（citations 已由呼叫端 remap document_id，顯式 created_at），回傳新 id。"""
    row = (
        await session.execute(
            text(
                """
                INSERT INTO messages
                    (conversation_id, role, content, citations, selection, token_usage,
                     created_at)
                VALUES (:conversation_id, :role, :content, :citations,
                        :selection, :token_usage, :created_at)
                RETURNING id
                """
            ),
            {
                "conversation_id": conversation_id,
                "role": role,
                "content": content,
                "citations": json.dumps(citations or []),
                "selection": json.dumps(selection) if selection is not None else None,
                "token_usage": json.dumps(token_usage or {}),
                "created_at": _coerce_ts(created_at),
            },
        )
    ).one()
    await session.commit()
    return int(row.id)


async def delete_chunks(session: AsyncSession, doc_id: int) -> None:
    """清空某文獻的 chunks（還原修復 failed 文獻時先清殘塊再重嵌，見 D11）。"""
    await session.execute(
        text("DELETE FROM chunks WHERE document_id = :doc_id"),
        {"doc_id": doc_id},
    )
    await session.commit()
