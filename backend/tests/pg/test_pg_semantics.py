"""Postgres 專有語意覆蓋（M15 T-FD-02，pg marker）。

SQLite 測試層抓不到這些：TIMESTAMPTZ 型別、JSONB 運算子聚合、pgvector `<=>` 檢索與
window function 防洗版、CHECK 約束、以及 dump→restore 六表往返。各測 1–2 個精準案例，
不求大而全——目標是「這些真 DB 行為至少有一條回歸線」。
"""

from __future__ import annotations

import json
import random

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db import repo
from app.services import backup, restore

_TS = "2026-03-01T00:00:00+00:00"
_EMBED_DIM = 1024


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _insert_doc(
    session,
    *,
    title="Doc",
    filename="paper.pdf",
    file_path="/data/uploads/x.pdf",
    status="ready",
    project_id=None,
) -> int:
    row = (
        await session.execute(
            text(
                """
                INSERT INTO documents
                    (user_id, project_id, title, filename, file_path, page_count, status)
                VALUES (1, :pid, :title, :fn, :fp, 3, :status)
                RETURNING id
                """
            ),
            {"pid": project_id, "title": title, "fn": filename, "fp": file_path, "status": status},
        )
    ).one()
    await session.commit()
    return int(row.id)


async def _seed_chunks_with_vectors(session, doc_id: int, vectors: list[list[float]]) -> list[int]:
    specs = [
        {
            "chunk_index": i,
            "page": 1,
            "section": "body",
            "content": f"doc{doc_id} chunk{i}",
            "bbox_list": [[0, i, 10, i + 5]],
        }
        for i in range(len(vectors))
    ]
    ids = await repo.insert_chunks(session, doc_id, specs)
    await repo.update_chunk_embeddings(session, ids, vectors)
    return ids


# ---------------------------------------------------------------------------
# 1. pgvector similar_chunks_scoped：doc scope 隔離 + 多篇 ROW_NUMBER 防洗版
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_similar_chunks_doc_scope_isolation(pg_db):
    session_maker, _ = pg_db
    rng = random.Random(1)
    q = [rng.uniform(-1, 1) for _ in range(_EMBED_DIM)]
    neg = [-x for x in q]  # 餘弦最遠

    async with session_maker() as session:
        doc_a = await _insert_doc(session, title="A", file_path="/data/uploads/a.pdf")
        doc_b = await _insert_doc(session, title="B", file_path="/data/uploads/b.pdf")
        await _seed_chunks_with_vectors(session, doc_a, [q] * 10)
        await _seed_chunks_with_vectors(session, doc_b, [neg] * 2)

        hits = await repo.similar_chunks_scoped(session, q, k=100, doc_id=doc_a)

    assert len(hits) == 10  # 只回 docA 的 chunk
    assert {h["document_id"] for h in hits} == {doc_a}  # docB 完全隔離


@pytest.mark.asyncio
async def test_similar_chunks_multi_doc_row_number_anti_flooding(pg_db):
    """library scope：單篇每篇最多 4 條（PARTITION BY document_id 的 ROW_NUMBER），
    避免最近的那篇把 k 個名額全洗掉，讓其他文獻仍能露出。"""
    session_maker, _ = pg_db
    rng = random.Random(2)
    q = [rng.uniform(-1, 1) for _ in range(_EMBED_DIM)]
    neg = [-x for x in q]

    async with session_maker() as session:
        doc_a = await _insert_doc(session, title="A", file_path="/data/uploads/a.pdf")
        doc_b = await _insert_doc(session, title="B", file_path="/data/uploads/b.pdf")
        # docA 10 個 chunk 全部貼齊 q（距離 0），docB 2 個較遠
        await _seed_chunks_with_vectors(session, doc_a, [q] * 10)
        await _seed_chunks_with_vectors(session, doc_b, [neg] * 2)

        hits = await repo.similar_chunks_scoped(session, q, k=5)

    doc_ids = [h["document_id"] for h in hits]
    assert len(hits) == 5
    # docA 至多 4 條（防洗版）；剩下名額讓 docB 露出——沒有 window function 會全是 docA
    assert doc_ids.count(doc_a) <= 4
    assert doc_b in doc_ids


# ---------------------------------------------------------------------------
# 1b. insert_chunks / update_chunk_embeddings 批次化（M15 T-FD-06）：真 Postgres 下
#     驗證多列 VALUES ... RETURNING 的順序保證，以及 executemany 批次 CAST(:emb AS
#     vector) 逐筆對應正確（SQLite 版 CAST 到未知型別會全部歸零，測不到這段真行為）。
# ---------------------------------------------------------------------------


def _chunk_specs(chunk_indexes: list[int]) -> list[dict]:
    return [
        {
            "chunk_index": idx,
            "page": 1,
            "section": None,
            "content": f"c{idx}",
            "bbox_list": [[0, idx, 10, idx + 5]],
        }
        for idx in chunk_indexes
    ]


@pytest.mark.asyncio
async def test_insert_chunks_batch_returns_ids_matching_scrambled_input_order(pg_db):
    """多列 VALUES INSERT ... RETURNING 的回傳 id 順序須對齊輸入 chunks 順序，即使
    chunk_index 刻意打亂（不依賴 RETURNING 在 Postgres 上的實作序）。"""
    session_maker, _ = pg_db
    async with session_maker() as session:
        doc_id = await _insert_doc(session)
        specs = _chunk_specs([4, 1, 3, 0, 2])
        ids = await repo.insert_chunks(session, doc_id, specs)

        rows = (
            await session.execute(
                text("SELECT id, chunk_index, content FROM chunks WHERE document_id = :d"),
                {"d": doc_id},
            )
        ).all()

    by_id = {r.id: (r.chunk_index, r.content) for r in rows}
    assert len(ids) == 5 == len(set(ids))
    for returned_id, spec in zip(ids, specs, strict=True):
        assert by_id[returned_id] == (spec["chunk_index"], spec["content"])


@pytest.mark.asyncio
async def test_insert_chunks_batches_over_500_on_real_postgres(pg_db):
    """>500 chunk 觸發分批（_INSERT_CHUNKS_BATCH_SIZE=500），驗證真 Postgres 下多批
    INSERT 仍收斂成正確、無缺漏、無重複的 id 對應。"""
    session_maker, _ = pg_db
    async with session_maker() as session:
        doc_id = await _insert_doc(session)
        specs = _chunk_specs(list(range(650)))
        ids = await repo.insert_chunks(session, doc_id, specs)

        count = (
            await session.execute(
                text("SELECT COUNT(*) FROM chunks WHERE document_id = :d"), {"d": doc_id}
            )
        ).scalar()

    assert len(ids) == 650 == len(set(ids))
    assert count == 650


@pytest.mark.asyncio
async def test_update_chunk_embeddings_batch_vector_cast_roundtrip(pg_db):
    """executemany 批次更新在真 Postgres 上，CAST(:emb AS vector) 必須逐筆對應正確——
    用 `<=>` 距離驗證每個 chunk 都拿到自己那組向量，而非彼此弄混或全部相同。"""
    session_maker, _ = pg_db
    rng = random.Random(3)
    vectors = [[rng.uniform(-1, 1) for _ in range(_EMBED_DIM)] for _ in range(5)]

    async with session_maker() as session:
        doc_id = await _insert_doc(session)
        ids = await _seed_chunks_with_vectors(session, doc_id, vectors)

        for chunk_id, vec in zip(ids, vectors, strict=True):
            hit = (
                await session.execute(
                    text(
                        """
                        SELECT id FROM chunks
                        WHERE document_id = :doc_id
                        ORDER BY embedding <=> CAST(:emb AS vector)
                        LIMIT 1
                        """
                    ),
                    {"doc_id": doc_id, "emb": json.dumps(vec)},
                )
            ).scalar()
            assert hit == chunk_id


# ---------------------------------------------------------------------------
# 1c. dump_chunks（M14 T-BK2-01，備份格式 v2）：`embedding::text` 讀回 pgvector 字面字串，
#     經 backup.vector_to_b64/b64_to_vector 往返後與原向量一致（float32 精度）；未嵌入
#     （NULL embedding）的 chunk 照出、embedding 欄為 None。SQLite 無 vector 型別測不到。
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dump_chunks_vector_roundtrip_and_null(pg_db):
    session_maker, _ = pg_db
    rng = random.Random(7)
    vectors = [[rng.uniform(-1, 1) for _ in range(_EMBED_DIM)] for _ in range(3)]

    async with session_maker() as session:
        doc_id = await _insert_doc(session, file_path="/data/uploads/uuidX.pdf")
        # 4 塊：前 3 塊填向量，第 4 塊留 NULL embedding（尚未嵌入）。
        ids = await repo.insert_chunks(session, doc_id, _chunk_specs([0, 1, 2, 3]))
        await repo.update_chunk_embeddings(session, ids[:3], vectors)

        rows = await repo.dump_chunks(session, doc_id)

    # ORDER BY chunk_index，欄位齊全
    assert [r["chunk_index"] for r in rows] == [0, 1, 2, 3]
    assert all(k in rows[0] for k in ("id", "page", "section", "content", "bbox_list", "embedding"))

    # 前 3 塊：embedding 為 pgvector text 字面字串，b64 往返精度 < 1e-6
    for r, vec in zip(rows[:3], vectors, strict=True):
        assert isinstance(r["embedding"], str)
        restored = backup.b64_to_vector(backup.vector_to_b64(r["embedding"]))
        assert len(restored) == _EMBED_DIM
        assert max(abs(a - b) for a, b in zip(vec, restored, strict=True)) < 1e-6

    # 第 4 塊未嵌入 → embedding 欄為 None（照出，不漏塊）
    assert rows[3]["embedding"] is None


# ---------------------------------------------------------------------------
# 2. total_token_usage：JSONB ->> / #>> 聚合（messages 逐則 + documents 導讀）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_total_token_usage_jsonb_aggregation(pg_db):
    session_maker, _ = pg_db
    async with session_maker() as session:
        doc_id = await _insert_doc(session)
        await repo.update_document_digest(
            session,
            doc_id,
            digest={"tldr": "x", "sections": []},
            usage={"prompt_tokens": 50, "completion_tokens": 5},
        )
        conv = await repo.create_conversation(
            session, scope="document", title="c", document_id=doc_id
        )
        await repo.add_message(
            session,
            conv["id"],
            "user",
            "hi",
            token_usage={"prompt_tokens": 100, "completion_tokens": 10},
        )
        await repo.add_message(
            session,
            conv["id"],
            "assistant",
            "yo",
            token_usage={"prompt_tokens": 200, "completion_tokens": 20},
        )

        totals = await repo.total_token_usage(session)

    assert totals == {"prompt_tokens": 100 + 200 + 50, "completion_tokens": 10 + 20 + 5}


# ---------------------------------------------------------------------------
# 3. conversations scope CHECK 約束（migration 002 的 chk_conversations_scope）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conversation_scope_check_rejects_invalid(pg_db):
    """scope='document' 卻無 document_id → CHECK 約束必須拒絕（SQLite 版無此約束，
    只有真 Postgres 攔得到）。"""
    session_maker, _ = pg_db
    async with session_maker() as session:
        with pytest.raises(IntegrityError):
            await repo.restore_insert_conversation(
                session,
                scope="document",
                document_id=None,  # 違反 chk_conversations_scope
                project_id=None,
                title="bad",
                model=None,
                created_at=_TS,
            )


# ---------------------------------------------------------------------------
# 4. TIMESTAMPTZ：restore_insert_annotation 收 ISO 字串正常入庫、讀回 aware
#    （M13 datetime 事故的直接回歸——asyncpg 對 TIMESTAMPTZ 拒收裸 ISO 字串）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timestamptz_restore_annotation_roundtrip(pg_db):
    session_maker, _ = pg_db
    iso = "2026-07-04T07:11:37.173945+00:00"
    async with session_maker() as session:
        doc_id = await _insert_doc(session)
        ann_id = await repo.restore_insert_annotation(
            session,
            document_id=doc_id,
            type="highlight",
            color="amber",
            page=1,
            bbox_list=[[0, 0, 10, 10]],
            selected_text="s",
            note_text="n",
            created_at=iso,
            updated_at=iso,
        )
        stored = (
            await session.execute(
                text("SELECT created_at, updated_at FROM annotations WHERE id = :id"),
                {"id": ann_id},
            )
        ).one()

    # asyncpg TIMESTAMPTZ 回 aware datetime；且同一瞬間
    assert stored.created_at.tzinfo is not None
    assert stored.updated_at.tzinfo is not None
    assert restore._parse_dt(stored.created_at) == restore._parse_dt(iso)


# ---------------------------------------------------------------------------
# 5. dump → restore 六表往返（縮小版，不走 gdrive）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dump_restore_roundtrip_six_tables(pg_db):
    """六張白名單表 dump_table_rows → 清庫 → restore_insert_* 灌回 → 內容一致。"""
    session_maker, engine = pg_db
    digest = {"tldr": "sum", "sections": [{"key": "m", "title": "M", "text": "t", "citations": []}]}

    # --- 種原始資料 ---
    async with session_maker() as session:
        proj_id = (
            await session.execute(
                text("INSERT INTO projects (user_id, name) VALUES (1, 'Proj') RETURNING id")
            )
        ).scalar()
        doc_id = (
            await session.execute(
                text(
                    """
                    INSERT INTO documents
                        (user_id, project_id, title, filename, file_path, page_count, status,
                         digest, token_usage)
                    VALUES (1, :pid, 'Paper', 'p.pdf', '/data/uploads/p.pdf', 3, 'ready',
                            CAST(:digest AS jsonb), CAST(:tu AS jsonb))
                    RETURNING id
                    """
                ),
                {"pid": proj_id, "digest": json.dumps(digest), "tu": "{}"},
            )
        ).scalar()
        await repo.create_annotation(
            session,
            doc_id,
            type="highlight",
            color="sage",
            page=1,
            bbox_list=[[0, 0, 10, 10]],
            selected_text="sel",
            note_text="the note",
        )
        await repo.create_glossary_entry(
            session,
            doc_id,
            term="term",
            translation="譯",
            target_lang="繁體中文",
            page=1,
            bbox_list=[[1, 1, 5, 5]],
            notes="n",
        )
        conv = await repo.create_conversation(
            session, scope="document", title="conv", document_id=doc_id
        )
        await repo.add_message(session, conv["id"], "user", "hello world", token_usage={})

    # --- dump 六表 ---
    async with session_maker() as session:
        dumps = {t: await repo.dump_table_rows(session, t) for t in backup.DUMP_TABLES}
    assert all(len(dumps[t]) == 1 for t in backup.DUMP_TABLES)

    # --- 清庫（保留 users）---
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE messages, conversations, annotations, glossary_entries, chunks, "
                "documents, projects RESTART IDENTITY CASCADE"
            )
        )

    # --- restore 灌回（id 全新，關聯欄位 remap）---
    async with session_maker() as session:
        p = dumps["projects"][0]
        new_proj = await repo.restore_insert_project(
            session, name=p["name"], created_at=p["created_at"]
        )
        d = dumps["documents"][0]
        new_doc = await repo.restore_insert_document(
            session,
            project_id=new_proj,
            title=d["title"],
            filename=d["filename"],
            file_path=d["file_path"],
            digest=d["digest"],
            token_usage=d["token_usage"],
            created_at=d["created_at"],
        )
        a = dumps["annotations"][0]
        await repo.restore_insert_annotation(
            session,
            document_id=new_doc,
            type=a["type"],
            color=a["color"],
            page=a["page"],
            bbox_list=a["bbox_list"],
            selected_text=a["selected_text"],
            note_text=a["note_text"],
            created_at=a["created_at"],
            updated_at=a["updated_at"],
        )
        g = dumps["glossary_entries"][0]
        await repo.restore_insert_glossary_entry(
            session,
            document_id=new_doc,
            term=g["term"],
            translation=g["translation"],
            target_lang=g["target_lang"],
            page=g["page"],
            bbox_list=g["bbox_list"],
            notes=g["notes"],
            created_at=g["created_at"],
        )
        c = dumps["conversations"][0]
        new_conv = await repo.restore_insert_conversation(
            session,
            scope=c["scope"],
            document_id=new_doc if c["document_id"] is not None else None,
            project_id=new_proj if c["project_id"] is not None else None,
            title=c["title"],
            model=c["model"],
            created_at=c["created_at"],
        )
        m = dumps["messages"][0]
        await repo.restore_insert_message(
            session,
            conversation_id=new_conv,
            role=m["role"],
            content=m["content"],
            citations=m["citations"],
            selection=m["selection"],
            token_usage=m["token_usage"],
            created_at=m["created_at"],
        )

    # --- 重新 dump 比對內容一致（id/chunk_id 除外）---
    async with session_maker() as session:
        redump = {t: await repo.dump_table_rows(session, t) for t in backup.DUMP_TABLES}

    assert all(len(redump[t]) == 1 for t in backup.DUMP_TABLES)
    assert redump["documents"][0]["title"] == "Paper"
    assert redump["documents"][0]["digest"] == digest  # JSONB 往返成 dict、內容一致
    assert redump["annotations"][0]["note_text"] == "the note"
    assert redump["annotations"][0]["chunk_id"] is None  # restore 一律清 chunk_id
    assert redump["glossary_entries"][0]["term"] == "term"
    assert redump["messages"][0]["content"] == "hello world"
    # 關聯完整性：conversation 指回新 document，message 指回新 conversation
    assert redump["conversations"][0]["document_id"] == new_doc
    assert redump["messages"][0]["conversation_id"] == redump["conversations"][0]["id"]
