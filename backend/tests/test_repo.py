"""repo 層 chunks_by_ids / chunks_by_indexes 測試（T-AN-08）。

原本兩函式用 Postgres-only 的 `= ANY(:list)`，SQLite 測試 DB 跑不動，
導致引用錨點鏈上的這兩個查詢零測試覆蓋。改用 SQLAlchemy expanding
bindparam 的 `IN :list` 後，兩函式在 SQLite/Postgres 皆可執行，
本檔補上真 DB 測試。
"""

import json

import pytest
from sqlalchemy import text

from app.db import repo


@pytest.mark.asyncio
class TestChunksByIds:
    async def test_multiple_ids_returns_all_hits(self, test_db, seeded_chunks):
        session_maker, _ = test_db
        chunks_a = seeded_chunks["chunks_a"]
        ids = [c["id"] for c in chunks_a]
        async with session_maker() as session:
            result = await repo.chunks_by_ids(session, seeded_chunks["doc_a"], ids)
        assert {r["id"] for r in result} == set(ids)
        assert len(result) == len(ids)

    async def test_document_title_join(self, test_db, seeded_chunks):
        session_maker, _ = test_db
        cid = seeded_chunks["chunks_a"][0]["id"]
        async with session_maker() as session:
            result = await repo.chunks_by_ids(session, seeded_chunks["doc_a"], [cid])
        assert result[0]["document_title"] == "Paper A"

    async def test_citation_anchor_fields_present(self, test_db, seeded_chunks):
        """引用錨點欄位（page/bbox_list/chunk_index）必須完整回傳（鐵律 1）。"""
        session_maker, _ = test_db
        spec = seeded_chunks["chunks_a"][1]
        async with session_maker() as session:
            result = await repo.chunks_by_ids(session, seeded_chunks["doc_a"], [spec["id"]])
        row = result[0]
        assert row["page"] == spec["page"]
        assert row["chunk_index"] == spec["chunk_index"]
        # SQLite 的 JSON 欄位回傳原始字串（不像 Postgres JSONB 會自動解析），
        # 測試 DB 下需自行 json.loads 才能跟 seed 的 Python list 比較。
        assert json.loads(row["bbox_list"]) == spec["bbox_list"]
        assert row["document_id"] == seeded_chunks["doc_a"]

    async def test_doc_id_isolation(self, test_db, seeded_chunks):
        """doc_id 隔離：即使把另一份文獻的 chunk id 混進查詢，也不得洩漏。"""
        session_maker, _ = test_db
        ids_a = [c["id"] for c in seeded_chunks["chunks_a"]]
        ids_b = [c["id"] for c in seeded_chunks["chunks_b"]]
        async with session_maker() as session:
            result = await repo.chunks_by_ids(session, seeded_chunks["doc_a"], ids_a + ids_b)
        result_ids = {r["id"] for r in result}
        assert result_ids == set(ids_a)
        assert result_ids.isdisjoint(set(ids_b))

    async def test_unknown_ids_silently_ignored(self, test_db, seeded_chunks):
        session_maker, _ = test_db
        real_id = seeded_chunks["chunks_a"][0]["id"]
        async with session_maker() as session:
            result = await repo.chunks_by_ids(session, seeded_chunks["doc_a"], [real_id, 999999])
        assert {r["id"] for r in result} == {real_id}

    async def test_empty_list_returns_empty(self, test_db, seeded_chunks):
        session_maker, _ = test_db
        async with session_maker() as session:
            result = await repo.chunks_by_ids(session, seeded_chunks["doc_a"], [])
        assert result == []


@pytest.mark.asyncio
class TestChunksByIndexes:
    async def test_sorted_by_chunk_index(self, test_db, seeded_chunks):
        session_maker, _ = test_db
        async with session_maker() as session:
            result = await repo.chunks_by_indexes(session, seeded_chunks["doc_a"], [2, 0, 1])
        assert [r["chunk_index"] for r in result] == [0, 1, 2]

    async def test_nonexistent_index_including_negative_silently_skipped(
        self, test_db, seeded_chunks
    ):
        """rag.py 的真實呼叫型態：selection 相鄰擴充可能帶 -1（sel_index - 1）。"""
        session_maker, _ = test_db
        async with session_maker() as session:
            result = await repo.chunks_by_indexes(session, seeded_chunks["doc_a"], [-1, 0, 999])
        assert [r["chunk_index"] for r in result] == [0]

    async def test_doc_id_isolation(self, test_db, seeded_chunks):
        """doc_id 隔離：文獻 B 的 chunk_index 0/1 不得混進文獻 A 的結果。"""
        session_maker, _ = test_db
        async with session_maker() as session:
            result = await repo.chunks_by_indexes(session, seeded_chunks["doc_a"], [0, 1])
        assert all(r["document_id"] == seeded_chunks["doc_a"] for r in result)
        assert len(result) == 2

    async def test_empty_list_returns_empty(self, test_db, seeded_chunks):
        session_maker, _ = test_db
        async with session_maker() as session:
            result = await repo.chunks_by_indexes(session, seeded_chunks["doc_a"], [])
        assert result == []


# ---------------------------------------------------------------------------
# insert_chunks / update_chunk_embeddings 批次化（M15 T-FD-06）：多列 INSERT ...
# RETURNING 取代逐列 INSERT、executemany 取代逐筆 UPDATE。round-trip 數用
# `_CountingSession` 直接量測（不靠計時，避免 flaky）。
# ---------------------------------------------------------------------------


async def _insert_bare_document(session_maker) -> int:
    """建一份不帶任何 chunk 的裸文獻，避免撞 conftest 預種的 document_id=1 / chunk_index=0。"""
    async with session_maker() as session:
        doc_id = (
            await session.execute(
                text(
                    """
                    INSERT INTO documents (user_id, title, filename, file_path, page_count, status)
                    VALUES (1, 'Bare', 'bare.pdf', '/tmp/bare.pdf', 1, 'uploaded')
                    RETURNING id
                    """
                )
            )
        ).scalar()
        await session.commit()
    return doc_id


def _make_chunk_specs(chunk_indexes: list[int]) -> list[dict]:
    return [
        {
            "chunk_index": idx,
            "page": 1,
            "section": None,
            "content": f"content-{idx}",
            "bbox_list": [[0.0, float(idx), 10.0, float(idx) + 5.0]],
        }
        for idx in chunk_indexes
    ]


class _CountingSession:
    """包一層計數 `execute` 呼叫次數，驗證批次化真的把 round-trip 收斂了。"""

    def __init__(self, session):
        self._session = session
        self.execute_calls = 0

    async def execute(self, *args, **kwargs):
        self.execute_calls += 1
        return await self._session.execute(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._session, name)


@pytest.mark.asyncio
class TestInsertChunksBatching:
    async def test_empty_list_short_circuits(self, test_db):
        session_maker, _ = test_db
        doc_id = await _insert_bare_document(session_maker)
        async with session_maker() as session:
            wrapped = _CountingSession(session)
            result = await repo.insert_chunks(wrapped, doc_id, [])
        assert result == []
        assert wrapped.execute_calls == 0

    async def test_return_order_matches_input_order(self, test_db):
        """回傳 id 清單順序 = 輸入 chunks 順序，即使 chunk_index 刻意打亂
        （不依賴多列 VALUES 下 RETURNING 的實作序，見 repo.insert_chunks docstring）。"""
        session_maker, _ = test_db
        doc_id = await _insert_bare_document(session_maker)
        specs = _make_chunk_specs([3, 0, 4, 1, 2])
        async with session_maker() as session:
            ids = await repo.insert_chunks(session, doc_id, specs)

        assert len(ids) == 5
        assert len(set(ids)) == 5  # 皆為相異 id

        async with session_maker() as session:
            rows = (
                await session.execute(
                    text("SELECT id, chunk_index, content FROM chunks WHERE document_id = :d"),
                    {"d": doc_id},
                )
            ).all()
        by_id = {r.id: (r.chunk_index, r.content) for r in rows}
        for returned_id, spec in zip(ids, specs, strict=True):
            assert by_id[returned_id] == (spec["chunk_index"], spec["content"])

    async def test_batches_over_500_split_into_multiple_statements(self, test_db):
        session_maker, _ = test_db
        doc_id = await _insert_bare_document(session_maker)
        specs = _make_chunk_specs(list(range(750)))  # > _INSERT_CHUNKS_BATCH_SIZE(500)
        async with session_maker() as session:
            wrapped = _CountingSession(session)
            ids = await repo.insert_chunks(wrapped, doc_id, specs)

        assert len(ids) == 750
        assert len(set(ids)) == 750
        assert wrapped.execute_calls == 2  # 500 + 250 兩批

        async with session_maker() as session:
            count = (
                await session.execute(
                    text("SELECT COUNT(*) FROM chunks WHERE document_id = :d"), {"d": doc_id}
                )
            ).scalar()
        assert count == 750


@pytest.mark.asyncio
class TestUpdateChunkEmbeddingsBatching:
    async def test_empty_list_short_circuits(self, test_db):
        session_maker, _ = test_db
        async with session_maker() as session:
            wrapped = _CountingSession(session)
            await repo.update_chunk_embeddings(wrapped, [], [])
        assert wrapped.execute_calls == 0

    async def test_single_execute_call_for_whole_batch(self, test_db):
        session_maker, _ = test_db
        doc_id = await _insert_bare_document(session_maker)
        specs = _make_chunk_specs(list(range(20)))
        async with session_maker() as session:
            chunk_ids = await repo.insert_chunks(session, doc_id, specs)

        embeddings = [[0.1, 0.2] for _ in chunk_ids]
        async with session_maker() as session:
            wrapped = _CountingSession(session)
            await repo.update_chunk_embeddings(wrapped, chunk_ids, embeddings)
        # 20 筆更新只發 1 次 execute（executemany 語意），取代逐筆 20 次 round-trip。
        assert wrapped.execute_calls == 1

    async def test_mismatched_lengths_raise(self, test_db):
        session_maker, _ = test_db
        doc_id = await _insert_bare_document(session_maker)
        specs = _make_chunk_specs([0, 1])
        async with session_maker() as session:
            chunk_ids = await repo.insert_chunks(session, doc_id, specs)
        async with session_maker() as session:
            with pytest.raises(ValueError):
                await repo.update_chunk_embeddings(session, chunk_ids, [[0.1, 0.2]])
