"""啟動時 reconciliation + /reingest 端點（M15 T-FD-01 / D4）。

ingest 走 BackgroundTask，程序中途被殺會讓文獻卡在 uploaded/parsing/embedding 這類
transient 非終態，永遠顯示處理中且無重試入口（uploaded 特別對應 restore 的 ingest phase
中斷——未輪到的文獻整批停在 uploaded；T-FD-99 審查補入）。這裡測兩件事：
1. `repo.reconcile_interrupted_ingests` 只轉 TRANSIENT_INGEST_STATUSES，不動 ready/failed。
2. `POST /api/documents/{id}/reingest` 的 404 / 409（該文獻進行中、全域 backup/restore
   進行中）/ 202 三態。
"""

import pytest
from sqlalchemy import text

from app.db import repo
from app.routers import documents as documents_router
from app.services import backup

# ---------------------------------------------------------------------------
# reconciliation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_interrupted_ingests_resets_only_transient_states(test_db):
    session_maker, _ = test_db
    async with session_maker() as s:
        for title, status in [
            ("Stuck parsing", "parsing"),
            ("Stuck embedding", "embedding"),
            ("Just uploaded", "uploaded"),
            ("Already failed", "failed"),
        ]:
            await s.execute(
                text(
                    """
                    INSERT INTO documents (user_id, title, filename, file_path, page_count, status)
                    VALUES (1, :title, 'p.pdf', '/tmp/p.pdf', 0, :status)
                    """
                ),
                {"title": title, "status": status},
            )
        await s.commit()

    async with session_maker() as s:
        n = await repo.reconcile_interrupted_ingests(s)

    assert n == 3  # uploaded + parsing + embedding 三筆被轉（uploaded 孤兒同屬中斷殘態）

    async with session_maker() as s:
        rows = (await s.execute(text("SELECT title, status, error_msg FROM documents"))).all()
    by_title = {r.title: r for r in rows}

    assert by_title["Stuck parsing"].status == "failed"
    assert by_title["Stuck parsing"].error_msg
    assert by_title["Stuck embedding"].status == "failed"
    assert by_title["Stuck embedding"].error_msg
    # 啟動時仍在 uploaded ＝ 背景 ingest 從未起跑（上傳或 restore 中斷），一樣要能救回
    assert by_title["Just uploaded"].status == "failed"
    assert by_title["Just uploaded"].error_msg
    # 終態不受影響（含 conftest 種好的 ready 文獻）
    assert by_title["Already failed"].status == "failed"
    assert by_title["Test Document"].status == "ready"


@pytest.mark.asyncio
async def test_reconcile_interrupted_ingests_noop_when_nothing_stuck(test_db):
    session_maker, _ = test_db
    async with session_maker() as s:
        n = await repo.reconcile_interrupted_ingests(s)
    assert n == 0


# ---------------------------------------------------------------------------
# POST /api/documents/{id}/reingest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reingest_endpoint_404_for_missing_document(async_client):
    resp = await async_client.post(
        "/api/documents/999999/reingest", headers={"Content-Type": "application/json"}
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_reingest_endpoint_409_when_document_already_ingesting(
    async_client, setup_test_document, test_db
):
    doc_id, _ = setup_test_document
    session_maker, _ = test_db
    async with session_maker() as s:
        await s.execute(
            text("UPDATE documents SET status = 'parsing' WHERE id = :d"), {"d": doc_id}
        )
        await s.commit()

    resp = await async_client.post(
        f"/api/documents/{doc_id}/reingest", headers={"Content-Type": "application/json"}
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "operation_running"


@pytest.mark.asyncio
async def test_reingest_endpoint_409_when_backup_or_restore_running(
    async_client, setup_test_document, monkeypatch
):
    doc_id, _ = setup_test_document
    monkeypatch.setattr(backup, "is_running", lambda: True)

    resp = await async_client.post(
        f"/api/documents/{doc_id}/reingest", headers={"Content-Type": "application/json"}
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "operation_running"


@pytest.mark.asyncio
async def test_reingest_endpoint_202_resets_status_and_schedules_ingest(
    async_client, setup_test_document, monkeypatch
):
    doc_id, _ = setup_test_document
    calls: list[tuple[int, bool]] = []

    async def fake_ingest_document(doc_id_arg: int, run_digest: bool = True) -> None:
        calls.append((doc_id_arg, run_digest))

    monkeypatch.setattr(documents_router, "ingest_document", fake_ingest_document)

    resp = await async_client.post(
        f"/api/documents/{doc_id}/reingest", headers={"Content-Type": "application/json"}
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["id"] == doc_id
    assert body["status"] == "parsing"
    assert "file_path" not in body
    assert calls == [(doc_id, True)]


# ---------------------------------------------------------------------------
# CSRF 最小防護：無 body 的 state-changing POST 要求 Content-Type: application/json
# （M15 T-FD-04 / require_json_content_type）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reingest_rejects_missing_json_content_type(async_client, setup_test_document):
    """缺 Content-Type: application/json → 400 json_required（在 404/409/業務邏輯之前擋下）。

    跨站 <form> POST 只能送 simple content-type，設不了 application/json（會觸發被擋的
    preflight）；缺 header 即視為此類請求，關閉「惡意網頁靜默觸發 reingest」的攻擊面。
    """
    doc_id, _ = setup_test_document
    # 完全不帶 Content-Type（httpx 對無 body 的 POST 不會自動補 application/json）
    resp = await async_client.post(f"/api/documents/{doc_id}/reingest")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "json_required"


@pytest.mark.asyncio
async def test_reingest_rejects_form_content_type(async_client, setup_test_document):
    """form-urlencoded（跨站表單能送的 content-type）同樣被擋。"""
    doc_id, _ = setup_test_document
    resp = await async_client.post(
        f"/api/documents/{doc_id}/reingest",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        content="foo=bar",
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "json_required"
