"""翻譯表（glossary）測試（T-TR-01）：LLM 呼叫一律 mock。"""

from unittest.mock import patch

import pytest

from app import settings_store
from app.db import repo
from app.services import glossary as glossary_service


@pytest.fixture(autouse=True)
def _reset_settings_cache(monkeypatch):
    """避免其他測試留下的 settings 快取污染 target_lang 回落邏輯。"""
    monkeypatch.setattr(settings_store, "_cache", {})


# ---------- service 層：create_entry ----------


@pytest.mark.asyncio
async def test_create_entry_success(test_db, setup_test_document):
    session_maker, _ = test_db
    doc_id, chunk_id = setup_test_document
    async with session_maker() as session:
        with patch(
            "app.services.glossary.chat",
            return_value=("神經網路", {"prompt_tokens": 1, "completion_tokens": 1}),
        ):
            entry = await glossary_service.create_entry(
                session,
                doc_id,
                term="neural network",
                page=1,
                bbox_list=[[0, 0, 10, 10]],
                chunk_id=chunk_id,
            )
    assert entry["term"] == "neural network"
    assert entry["translation"] == "神經網路"
    assert entry["target_lang"] == "繁體中文"
    assert entry["chunk_id"] == chunk_id
    assert "id" in entry


@pytest.mark.asyncio
async def test_create_entry_no_chunk_id(test_db, setup_test_document):
    """無 chunk_id 時不查上下文，仍可翻譯建立。"""
    session_maker, _ = test_db
    doc_id, _ = setup_test_document
    async with session_maker() as session:
        with patch(
            "app.services.glossary.chat",
            return_value=("梯度下降", {}),
        ):
            entry = await glossary_service.create_entry(
                session,
                doc_id,
                term="gradient descent",
                page=2,
                bbox_list=[[0, 0, 10, 10]],
            )
    assert entry["translation"] == "梯度下降"
    assert entry["chunk_id"] is None


@pytest.mark.asyncio
async def test_create_entry_llm_failure_still_creates(test_db, setup_test_document):
    """LLM 擲例外時條目仍建立，translation 為空字串（不得 500）。"""
    session_maker, _ = test_db
    doc_id, chunk_id = setup_test_document
    async with session_maker() as session:
        with patch(
            "app.services.glossary.chat",
            side_effect=RuntimeError("llm api down"),
        ):
            entry = await glossary_service.create_entry(
                session,
                doc_id,
                term="overfitting",
                page=1,
                bbox_list=[[0, 0, 10, 10]],
                chunk_id=chunk_id,
            )
    assert entry["translation"] == ""
    assert "id" in entry


@pytest.mark.asyncio
async def test_target_lang_falls_back_to_default(test_db, setup_test_document):
    """未設定 translation_target_lang 時回落「繁體中文」。"""
    session_maker, _ = test_db
    doc_id, _ = setup_test_document
    async with session_maker() as session:
        with patch("app.services.glossary.chat", return_value=("x", {})):
            entry = await glossary_service.create_entry(
                session, doc_id, term="loss", page=1, bbox_list=[[0, 0, 10, 10]]
            )
    assert entry["target_lang"] == "繁體中文"


@pytest.mark.asyncio
async def test_target_lang_uses_setting(test_db, setup_test_document, monkeypatch):
    """設定 translation_target_lang 存在時使用該值。"""
    monkeypatch.setattr(settings_store, "_cache", {"translation_target_lang": "English"})
    session_maker, _ = test_db
    doc_id, _ = setup_test_document
    async with session_maker() as session:
        with patch("app.services.glossary.chat", return_value=("loss function", {})):
            entry = await glossary_service.create_entry(
                session, doc_id, term="損失函數", page=1, bbox_list=[[0, 0, 10, 10]]
            )
    assert entry["target_lang"] == "English"
    assert entry["translation"] == "loss function"


# ---------- service 層：retranslate ----------


@pytest.mark.asyncio
async def test_retranslate_updates_translation(test_db, setup_test_document):
    session_maker, _ = test_db
    doc_id, chunk_id = setup_test_document
    async with session_maker() as session:
        with patch("app.services.glossary.chat", return_value=("舊譯文", {})):
            entry = await glossary_service.create_entry(
                session,
                doc_id,
                term="epoch",
                page=1,
                bbox_list=[[0, 0, 10, 10]],
                chunk_id=chunk_id,
            )
        with patch("app.services.glossary.chat", return_value=("新譯文", {})):
            updated = await glossary_service.retranslate(session, entry["id"])
    assert updated["translation"] == "新譯文"


@pytest.mark.asyncio
async def test_retranslate_not_found(test_db):
    session_maker, _ = test_db
    async with session_maker() as session:
        result = await glossary_service.retranslate(session, 999999)
    assert result is None


@pytest.mark.asyncio
async def test_retranslate_llm_failure_keeps_old_translation(test_db, setup_test_document):
    session_maker, _ = test_db
    doc_id, chunk_id = setup_test_document
    async with session_maker() as session:
        with patch("app.services.glossary.chat", return_value=("原譯文", {})):
            entry = await glossary_service.create_entry(
                session,
                doc_id,
                term="batch",
                page=1,
                bbox_list=[[0, 0, 10, 10]],
                chunk_id=chunk_id,
            )
        with patch("app.services.glossary.chat", side_effect=RuntimeError("down")):
            result = await glossary_service.retranslate(session, entry["id"])
    assert result["translation"] == "原譯文"


# ---------- router 層：CRUD roundtrip ----------


@pytest.mark.asyncio
async def test_router_create_list_delete_roundtrip(async_client, setup_test_document):
    doc_id, chunk_id = setup_test_document
    with patch("app.services.glossary.chat", return_value=("測試譯文", {})):
        resp = await async_client.post(
            f"/api/documents/{doc_id}/glossary",
            json={
                "term": "test term",
                "page": 1,
                "bbox_list": [[0, 0, 10, 10]],
                "chunk_id": chunk_id,
            },
        )
    assert resp.status_code == 201
    entry = resp.json()
    assert entry["translation"] == "測試譯文"
    entry_id = entry["id"]

    resp = await async_client.get(f"/api/documents/{doc_id}/glossary")
    assert resp.status_code == 200
    entries = resp.json()
    assert len(entries) == 1
    assert entries[0]["id"] == entry_id

    resp = await async_client.delete(f"/api/glossary/{entry_id}")
    assert resp.status_code == 204

    resp = await async_client.get(f"/api/documents/{doc_id}/glossary")
    assert resp.json() == []


@pytest.mark.asyncio
async def test_router_create_document_not_found(async_client):
    resp = await async_client.post(
        "/api/documents/999999/glossary",
        json={"term": "x", "page": 1, "bbox_list": [[0, 0, 10, 10]]},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_router_list_document_not_found(async_client):
    resp = await async_client.get("/api/documents/999999/glossary")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_router_delete_not_found(async_client):
    resp = await async_client.delete("/api/glossary/999999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_router_retranslate_not_found(async_client):
    resp = await async_client.post("/api/glossary/999999/retranslate")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_router_retranslate_roundtrip(async_client, setup_test_document):
    doc_id, chunk_id = setup_test_document
    with patch("app.services.glossary.chat", return_value=("第一版", {})):
        resp = await async_client.post(
            f"/api/documents/{doc_id}/glossary",
            json={"term": "x", "page": 1, "bbox_list": [[0, 0, 10, 10]], "chunk_id": chunk_id},
        )
    entry_id = resp.json()["id"]

    with patch("app.services.glossary.chat", return_value=("第二版", {})):
        resp = await async_client.post(f"/api/glossary/{entry_id}/retranslate")
    assert resp.status_code == 200
    assert resp.json()["translation"] == "第二版"


@pytest.mark.asyncio
async def test_router_empty_bbox_list_422(async_client, setup_test_document):
    doc_id, _ = setup_test_document
    resp = await async_client.post(
        f"/api/documents/{doc_id}/glossary",
        json={"term": "x", "page": 1, "bbox_list": []},
    )
    assert resp.status_code == 422


# ---------- repo 層 ----------


@pytest.mark.asyncio
async def test_repo_create_and_get_glossary_entry(test_db, setup_test_document):
    session_maker, _ = test_db
    doc_id, chunk_id = setup_test_document
    async with session_maker() as session:
        entry = await repo.create_glossary_entry(
            session,
            doc_id,
            term="term",
            translation="譯文",
            target_lang="繁體中文",
            page=1,
            bbox_list=[[0, 0, 10, 10]],
            chunk_id=chunk_id,
        )
        fetched = await repo.get_glossary_entry(session, entry["id"])
    assert fetched["term"] == "term"
    assert fetched["translation"] == "譯文"


@pytest.mark.asyncio
async def test_repo_get_glossary_entry_not_found(test_db):
    session_maker, _ = test_db
    async with session_maker() as session:
        result = await repo.get_glossary_entry(session, 999999)
    assert result is None
