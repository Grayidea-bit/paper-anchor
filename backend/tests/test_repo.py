"""repo 層 chunks_by_ids / chunks_by_indexes 測試（T-AN-08）。

原本兩函式用 Postgres-only 的 `= ANY(:list)`，SQLite 測試 DB 跑不動，
導致引用錨點鏈上的這兩個查詢零測試覆蓋。改用 SQLAlchemy expanding
bindparam 的 `IN :list` 後，兩函式在 SQLite/Postgres 皆可執行，
本檔補上真 DB 測試。
"""

import json

import pytest

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
