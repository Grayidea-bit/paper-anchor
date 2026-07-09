"""reembed 維護動作測試（M14 D12 / T-EM-02）。

鎖互斥手法同 `test_backup.py`：直接持有 `backup._lock` 模擬「已有操作在跑」，
或 monkeypatch `backup.is_running` 模擬 router 層 409 判斷。`services/reembed.py`
的 `SessionLocal` 換成測試 session_maker（同 `test_backup.py` 的 `orchestration_db`
手法），`embed_passages`/`repo.update_chunk_embeddings` 全 mock（鐵律 3：LLM 呼叫一律
mock）。
"""

import pytest
from sqlalchemy import text

from app.routers import maintenance as maintenance_router
from app.services import backup, reembed

# ---------- 共用 fixture ----------


@pytest.fixture(autouse=True)
def _reset_backup_module_state():
    """每測試重置 backup 模組級狀態（鎖由 `async with` 保證釋放，這裡只清進度/operation）。"""
    backup._progress = None
    backup._operation = None
    yield
    backup._progress = None
    backup._operation = None


async def _seed_document(
    session_maker, *, title: str, status: str = "ready", chunk_contents: list[str] | None = None
) -> int:
    """建立一篇文獻（預設 ready）與其 chunks，回傳 document id。"""
    async with session_maker() as session:
        doc_id = (
            await session.execute(
                text(
                    """
                    INSERT INTO documents
                        (user_id, title, filename, file_path, page_count, status)
                    VALUES (1, :title, :filename, :file_path, 1, :status)
                    RETURNING id
                    """
                ),
                {
                    "title": title,
                    "filename": f"{title}.pdf",
                    "file_path": f"/tmp/{title}.pdf",
                    "status": status,
                },
            )
        ).scalar()
        for i, content in enumerate(chunk_contents or []):
            await session.execute(
                text(
                    """
                    INSERT INTO chunks (document_id, chunk_index, page, section, content, bbox_list)
                    VALUES (:doc_id, :idx, 1, 'body', :content, '[]')
                    """
                ),
                {"doc_id": doc_id, "idx": i, "content": content},
            )
        await session.commit()
    return doc_id


@pytest.fixture
async def reembed_db(test_db, monkeypatch):
    """把 `services/reembed.py` 的 SessionLocal 換成測試 session_maker。

    `test_db` 本身預先插了一篇 status='ready' 的預設文獻與一個 chunk（供其他測試檔共用），
    這裡先清空，讓本檔測試的文獻/chunk 計數不被那筆預設資料汙染。
    """
    session_maker, _ = test_db
    monkeypatch.setattr(reembed, "SessionLocal", session_maker)
    async with session_maker() as session:
        await session.execute(text("DELETE FROM chunks"))
        await session.execute(text("DELETE FROM documents"))
        await session.commit()
    return session_maker


# ---------- run_reembed 編排 ----------


class TestRunReembedOrchestration:
    async def test_noop_when_another_operation_running(self, reembed_db, monkeypatch):
        """backup._lock 已被佔用時，run_reembed 直接 no-op（不查文獻、不呼叫 embed_passages）。"""
        called = {"n": 0}

        async def _fake_embed(texts):
            called["n"] += 1
            return [[0.0] for _ in texts]

        monkeypatch.setattr(reembed, "embed_passages", _fake_embed)

        async with backup._lock:
            await reembed.run_reembed()

        assert called["n"] == 0
        assert backup._operation is None

    async def test_progress_sequence_per_document(self, reembed_db, monkeypatch):
        """逐篇（以「篇」為單位）更新 operation/progress，兩篇 ready 文獻應各推進一次。"""
        await _seed_document(reembed_db, title="doc-a", chunk_contents=["hello", "world"])
        await _seed_document(reembed_db, title="doc-b", chunk_contents=["foo"])

        snapshots: list[dict] = []
        original_set_progress = backup.set_progress

        def _recording_set_progress(phase, current, total):
            original_set_progress(phase, current, total)
            snapshots.append({"phase": phase, "current": current, "total": total})

        monkeypatch.setattr(backup, "set_progress", _recording_set_progress)

        async def _fake_embed(texts):
            return [[0.1] * 4 for _ in texts]

        embed_calls: list[list[str]] = []

        async def _tracking_embed(texts):
            embed_calls.append(list(texts))
            return await _fake_embed(texts)

        monkeypatch.setattr(reembed, "embed_passages", _tracking_embed)

        await reembed.run_reembed()

        # 進度以篇為單位：0/2 開頭、1/2、2/2 結尾（依插入序，doc-a 先、doc-b 後）。
        assert snapshots[0] == {"phase": "reembed", "current": 0, "total": 2}
        assert snapshots[-1] == {"phase": "reembed", "current": 2, "total": 2}
        assert [s["current"] for s in snapshots] == [0, 1, 2]

        # 兩篇的 chunk content 各自送去 embed_passages 一次；`list_documents` 依
        # created_at DESC 排序，同一測試內兩篇建立時間可能落在同一秒（SQLite 解析度），
        # 順序不保證——只斷言兩批各自完整、無交錯。
        assert sorted(embed_calls) == sorted([["hello", "world"], ["foo"]])

        # 完成後鎖釋放、進度/operation 清空（同 backup/restore 慣例）。
        assert backup.is_running() is False
        assert backup._progress is None
        assert backup._operation is None

    async def test_only_ready_documents_are_reembedded(self, reembed_db, monkeypatch):
        """failed/uploaded 等非 ready 文獻不進 reembed（走既有 reingest 路徑修復）。"""
        await _seed_document(reembed_db, title="ready-doc", status="ready", chunk_contents=["x"])
        await _seed_document(reembed_db, title="failed-doc", status="failed", chunk_contents=["y"])

        embed_calls: list[list[str]] = []

        async def _tracking_embed(texts):
            embed_calls.append(list(texts))
            return [[0.1] * 4 for _ in texts]

        monkeypatch.setattr(reembed, "embed_passages", _tracking_embed)

        await reembed.run_reembed()

        assert embed_calls == [["x"]]

    async def test_single_document_failure_continues_batch(self, reembed_db, monkeypatch):
        """一篇 embed_passages 拋例外時記 log 續跑，不影響後續文獻。"""
        await _seed_document(reembed_db, title="bad-doc", chunk_contents=["boom"])
        await _seed_document(reembed_db, title="good-doc", chunk_contents=["ok"])

        async def _flaky_embed(texts):
            if texts == ["boom"]:
                raise RuntimeError("模擬 embed 失敗")
            return [[0.1] * 4 for _ in texts]

        monkeypatch.setattr(reembed, "embed_passages", _flaky_embed)

        update_calls: list[tuple[list[int], list[list[float]]]] = []

        from app.db import repo as repo_module

        original_update = repo_module.update_chunk_embeddings

        async def _tracking_update(session, chunk_ids, embeddings):
            update_calls.append((chunk_ids, embeddings))
            return await original_update(session, chunk_ids, embeddings)

        monkeypatch.setattr(repo_module, "update_chunk_embeddings", _tracking_update)

        await reembed.run_reembed()

        # 壞的那篇沒有寫入向量，好的那篇正常寫入——批次沒有整批中止。
        assert len(update_calls) == 1
        assert backup.is_running() is False

    async def test_document_with_no_chunks_is_skipped_without_error(self, reembed_db, monkeypatch):
        """ready 但沒有任何 chunk 的文獻（邊緣情況）直接跳過，不呼叫 embed_passages。"""
        await _seed_document(reembed_db, title="empty-doc", chunk_contents=[])

        called = {"n": 0}

        async def _fake_embed(texts):
            called["n"] += 1
            return [[0.0] for _ in texts]

        monkeypatch.setattr(reembed, "embed_passages", _fake_embed)

        await reembed.run_reembed()

        assert called["n"] == 0
        assert backup.is_running() is False


# ---------- repo.get_chunks limit 行為 ----------


class TestGetChunksLimit:
    async def test_limit_none_returns_all_chunks(self, test_db):
        session_maker, _ = test_db
        doc_id = await _seed_document(
            session_maker, title="many-chunks", chunk_contents=[f"c{i}" for i in range(5)]
        )

        from app.db import repo as repo_module

        async with session_maker() as session:
            chunks = await repo_module.get_chunks(session, doc_id, limit=None)

        assert len(chunks) == 5

    async def test_explicit_limit_truncates(self, test_db):
        session_maker, _ = test_db
        doc_id = await _seed_document(
            session_maker, title="many-chunks-2", chunk_contents=[f"c{i}" for i in range(5)]
        )

        from app.db import repo as repo_module

        async with session_maker() as session:
            chunks = await repo_module.get_chunks(session, doc_id, limit=2)

        assert len(chunks) == 2

    async def test_default_limit_still_applies(self, test_db):
        """既有呼叫點（未帶 limit）行為不變：預設仍是 500，不是全取。"""
        session_maker, _ = test_db
        doc_id = await _seed_document(
            session_maker, title="default-limit-doc", chunk_contents=["only-one"]
        )

        from app.db import repo as repo_module

        async with session_maker() as session:
            chunks = await repo_module.get_chunks(session, doc_id)

        assert len(chunks) == 1


# ---------- routers/maintenance.py 端點 ----------


class TestReembedEndpoint:
    async def test_returns_202_when_idle(self, async_client, monkeypatch):
        called = {"n": 0}

        async def _noop():
            called["n"] += 1

        monkeypatch.setattr(maintenance_router.reembed, "run_reembed", _noop)
        resp = await async_client.post(
            "/api/maintenance/reembed", headers={"Content-Type": "application/json"}
        )
        assert resp.status_code == 202
        assert resp.json() == {"started": True}
        assert called["n"] == 1

    async def test_returns_409_when_backup_or_restore_running(self, async_client, monkeypatch):
        monkeypatch.setattr(maintenance_router.backup, "is_running", lambda: True)
        resp = await async_client.post(
            "/api/maintenance/reembed", headers={"Content-Type": "application/json"}
        )
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "operation_running"

    async def test_returns_400_without_json_content_type(self, async_client):
        resp = await async_client.post("/api/maintenance/reembed")
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "json_required"
