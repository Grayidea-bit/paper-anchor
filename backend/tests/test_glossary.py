"""翻譯表（glossary）測試（T-TR-01 / T-TR-04）：LLM 呼叫一律 mock。"""

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
    assert entry["notes"] == ""
    assert "id" in entry


@pytest.mark.asyncio
async def test_create_entry_with_source_text_parses_translation_and_notes(
    test_db, setup_test_document
):
    """帶 source_text：mock 回兩行固定格式 → translation/notes 正確入庫。"""
    session_maker, _ = test_db
    doc_id, chunk_id = setup_test_document
    async with session_maker() as session:
        with patch(
            "app.services.glossary.chat",
            return_value=("譯文：神經網路\n註解：一種模仿生物神經系統結構的機器學習模型。", {}),
        ) as mock_chat:
            entry = await glossary_service.create_entry(
                session,
                doc_id,
                term="neural network",
                page=1,
                bbox_list=[[0, 0, 10, 10]],
                chunk_id=chunk_id,
                source_text="Neural network（神經網路）是一種...詳細翻譯全文...",
            )
    assert entry["translation"] == "神經網路"
    assert entry["notes"] == "一種模仿生物神經系統結構的機器學習模型。"
    assert mock_chat.await_count == 1


@pytest.mark.asyncio
async def test_create_entry_with_source_text_malformed_output_degrades(
    test_db, setup_test_document
):
    """帶 source_text 但 mock 回覆不合「譯文：/註解：」格式 → 降級整段當譯文，notes 空。"""
    session_maker, _ = test_db
    doc_id, chunk_id = setup_test_document
    async with session_maker() as session:
        with patch(
            "app.services.glossary.chat",
            return_value=("這是一段沒有照格式回覆的自由文字說明", {}),
        ):
            entry = await glossary_service.create_entry(
                session,
                doc_id,
                term="gradient",
                page=1,
                bbox_list=[[0, 0, 10, 10]],
                chunk_id=chunk_id,
                source_text="詳細翻譯全文...",
            )
    assert entry["translation"] == "這是一段沒有照格式回覆的自由文字說明"
    assert entry["notes"] == ""


@pytest.mark.asyncio
async def test_create_entry_with_source_text_llm_failure_degrades_both_empty(
    test_db, setup_test_document
):
    """帶 source_text 時 LLM 例外：條目仍建立，translation/notes 皆空。"""
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
                term="backprop",
                page=1,
                bbox_list=[[0, 0, 10, 10]],
                chunk_id=chunk_id,
                source_text="詳細翻譯全文...",
            )
    assert entry["translation"] == ""
    assert entry["notes"] == ""
    assert "id" in entry


@pytest.mark.asyncio
async def test_create_entry_without_source_text_notes_empty(test_db, setup_test_document):
    """不帶 source_text：行為與既有 fallback 路徑一致，notes 一律空字串。"""
    session_maker, _ = test_db
    doc_id, chunk_id = setup_test_document
    async with session_maker() as session:
        with patch(
            "app.services.glossary.chat",
            return_value=("神經網路", {}),
        ):
            entry = await glossary_service.create_entry(
                session,
                doc_id,
                term="neural network",
                page=1,
                bbox_list=[[0, 0, 10, 10]],
                chunk_id=chunk_id,
            )
    assert entry["translation"] == "神經網路"
    assert entry["notes"] == ""


@pytest.mark.asyncio
async def test_create_entry_with_frontend_provided_translation_and_notes_skips_llm(
    test_db, setup_test_document
):
    """優先序 1：前端直接提供 translation+notes → 直存，llm.chat 不被呼叫。"""
    session_maker, _ = test_db
    doc_id, chunk_id = setup_test_document
    async with session_maker() as session:
        with patch("app.services.glossary.chat") as mock_chat:
            entry = await glossary_service.create_entry(
                session,
                doc_id,
                term="neural network",
                page=1,
                bbox_list=[[0, 0, 10, 10]],
                chunk_id=chunk_id,
                translation="神經網路",
                notes="一種模仿生物神經系統結構的機器學習模型。",
            )
    assert entry["translation"] == "神經網路"
    assert entry["notes"] == "一種模仿生物神經系統結構的機器學習模型。"
    mock_chat.assert_not_called()


@pytest.mark.asyncio
async def test_create_entry_with_frontend_provided_translation_only_notes_empty(
    test_db, setup_test_document
):
    """優先序 1：前端只提供 translation（notes=None）→ 直存 translation，notes 變空字串。"""
    session_maker, _ = test_db
    doc_id, chunk_id = setup_test_document
    async with session_maker() as session:
        with patch("app.services.glossary.chat") as mock_chat:
            entry = await glossary_service.create_entry(
                session,
                doc_id,
                term="backpropagation",
                page=1,
                bbox_list=[[0, 0, 10, 10]],
                chunk_id=chunk_id,
                translation="反向傳播",
            )
    assert entry["translation"] == "反向傳播"
    assert entry["notes"] == ""
    mock_chat.assert_not_called()


@pytest.mark.asyncio
async def test_create_entry_with_frontend_provided_translation_empty_notes(
    test_db, setup_test_document
):
    """優先序 1：前端提供 translation 與 notes=""（顯示提供空字串）→ 兩者都直存。"""
    session_maker, _ = test_db
    doc_id, chunk_id = setup_test_document
    async with session_maker() as session:
        with patch("app.services.glossary.chat") as mock_chat:
            entry = await glossary_service.create_entry(
                session,
                doc_id,
                term="epoch",
                page=1,
                bbox_list=[[0, 0, 10, 10]],
                chunk_id=chunk_id,
                translation="時代",
                notes="",
            )
    assert entry["translation"] == "時代"
    assert entry["notes"] == ""
    mock_chat.assert_not_called()


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
async def test_router_create_with_source_text_extracts_notes(async_client, setup_test_document):
    """帶 source_text 建立條目：譯文＋註解一起入庫並可經 GET 讀回。"""
    doc_id, chunk_id = setup_test_document
    with patch(
        "app.services.glossary.chat",
        return_value=("譯文：梯度下降\n註解：一種透過反覆調整參數最小化損失函數的最佳化方法。", {}),
    ):
        resp = await async_client.post(
            f"/api/documents/{doc_id}/glossary",
            json={
                "term": "gradient descent",
                "page": 1,
                "bbox_list": [[0, 0, 10, 10]],
                "chunk_id": chunk_id,
                "source_text": "Gradient descent 詳細翻譯：這是一種最佳化演算法...",
            },
        )
    assert resp.status_code == 201
    entry = resp.json()
    assert entry["translation"] == "梯度下降"
    assert entry["notes"] == "一種透過反覆調整參數最小化損失函數的最佳化方法。"

    resp = await async_client.get(f"/api/documents/{doc_id}/glossary")
    assert resp.json()[0]["notes"] == "一種透過反覆調整參數最小化損失函數的最佳化方法。"


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
    resp = await async_client.post(
        "/api/glossary/999999/retranslate", headers={"Content-Type": "application/json"}
    )
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
        resp = await async_client.post(
            f"/api/glossary/{entry_id}/retranslate",
            headers={"Content-Type": "application/json"},
        )
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


@pytest.mark.asyncio
async def test_router_create_with_frontend_translation_and_notes_no_llm_call(
    async_client, setup_test_document
):
    """POST 帶 translation+notes：直存，LLM 不被呼叫。"""
    doc_id, chunk_id = setup_test_document
    with patch("app.services.glossary.chat") as mock_chat:
        resp = await async_client.post(
            f"/api/documents/{doc_id}/glossary",
            json={
                "term": "convolutional neural network",
                "page": 1,
                "bbox_list": [[0, 0, 10, 10]],
                "chunk_id": chunk_id,
                "translation": "卷積神經網路",
                "notes": "一種使用卷積層進行特徵提取的神經網路架構。",
            },
        )
    assert resp.status_code == 201
    entry = resp.json()
    assert entry["translation"] == "卷積神經網路"
    assert entry["notes"] == "一種使用卷積層進行特徵提取的神經網路架構。"
    mock_chat.assert_not_called()


@pytest.mark.asyncio
async def test_router_create_with_frontend_translation_only_notes_defaults_to_empty(
    async_client, setup_test_document
):
    """POST 帶 translation（不帶 notes）：translation 直存，notes 變空字串。"""
    doc_id, chunk_id = setup_test_document
    with patch("app.services.glossary.chat") as mock_chat:
        resp = await async_client.post(
            f"/api/documents/{doc_id}/glossary",
            json={
                "term": "recurrent neural network",
                "page": 1,
                "bbox_list": [[0, 0, 10, 10]],
                "chunk_id": chunk_id,
                "translation": "遞迴神經網路",
            },
        )
    assert resp.status_code == 201
    entry = resp.json()
    assert entry["translation"] == "遞迴神經網路"
    assert entry["notes"] == ""
    mock_chat.assert_not_called()


@pytest.mark.asyncio
async def test_router_create_translation_field_too_long_422(async_client, setup_test_document):
    """translation 超過 500 字元 → 422。"""
    doc_id, chunk_id = setup_test_document
    long_translation = "a" * 501
    resp = await async_client.post(
        f"/api/documents/{doc_id}/glossary",
        json={
            "term": "test",
            "page": 1,
            "bbox_list": [[0, 0, 10, 10]],
            "chunk_id": chunk_id,
            "translation": long_translation,
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_router_create_notes_field_too_long_422(async_client, setup_test_document):
    """notes 超過 12000 字元 → 422。"""
    doc_id, chunk_id = setup_test_document
    long_notes = "a" * 12001
    resp = await async_client.post(
        f"/api/documents/{doc_id}/glossary",
        json={
            "term": "test",
            "page": 1,
            "bbox_list": [[0, 0, 10, 10]],
            "chunk_id": chunk_id,
            "translation": "譯文",
            "notes": long_notes,
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_router_create_with_frontend_translation_notes_persists_and_reads_back(
    async_client, setup_test_document
):
    """POST 帶 translation+notes，GET 回來確認都保存。"""
    doc_id, chunk_id = setup_test_document
    with patch("app.services.glossary.chat"):
        resp = await async_client.post(
            f"/api/documents/{doc_id}/glossary",
            json={
                "term": "attention mechanism",
                "page": 1,
                "bbox_list": [[0, 0, 10, 10]],
                "chunk_id": chunk_id,
                "translation": "注意力機制",
                "notes": "一種允許模型關注輸入中特定部分的技術。",
            },
        )
    entry_id = resp.json()["id"]

    # GET 檢驗
    resp = await async_client.get(f"/api/documents/{doc_id}/glossary")
    assert resp.status_code == 200
    entries = resp.json()
    assert len(entries) == 1
    assert entries[0]["id"] == entry_id
    assert entries[0]["translation"] == "注意力機制"
    assert entries[0]["notes"] == "一種允許模型關注輸入中特定部分的技術。"


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
    assert fetched["notes"] == ""


@pytest.mark.asyncio
async def test_repo_get_glossary_entry_not_found(test_db):
    session_maker, _ = test_db
    async with session_maker() as session:
        result = await repo.get_glossary_entry(session, 999999)
    assert result is None


@pytest.mark.asyncio
async def test_repo_create_and_get_glossary_entry_with_notes(test_db, setup_test_document):
    """notes 欄位可正確寫入與讀回（T-TR-04）。"""
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
            notes="一句白話補充",
        )
        fetched = await repo.get_glossary_entry(session, entry["id"])
    assert entry["notes"] == "一句白話補充"
    assert fetched["notes"] == "一句白話補充"
