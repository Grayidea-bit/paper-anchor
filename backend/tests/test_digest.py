"""digest.py 測試：T-DG-01 chat_once 分派 + extract_json/_validate 管線一字不動守門。

update_document_digest 用 Postgres 專有 CAST(... AS jsonb) 語法（SQLite 測試 DB 不支援，
同 test_ingest.py 對 update_chunk_embeddings 的處理方式），故此處 mock 掉、只驗證
generate_digest 傳給它的 (digest, usage) 內容是否正確——DB 寫入語法本身由 tests/pg 覆蓋。
"""

import pytest

from app.db import repo
from app.services import digest as digest_mod
from app.services.digest import _select_chunks, generate_digest


@pytest.fixture
def wire_digest_db(test_db, monkeypatch):
    session_maker, _ = test_db
    monkeypatch.setattr(digest_mod, "SessionLocal", session_maker)
    return session_maker


def _fenced_json_reply() -> str:
    """Claude 後端典型回覆樣式：```json 圍欄 + 前後可能有雜訊。"""
    return (
        "這是導讀結果：\n"
        "```json\n"
        '{"tldr": "一句話總結", "sections": ['
        '{"key": "research_question", "title": "研究問題", "text": "t1", "citations": [0]},'
        '{"key": "method", "title": "方法", "text": "t2", "citations": [0]},'
        '{"key": "findings", "title": "主要發現", "text": "t3", "citations": [0]},'
        '{"key": "contributions", "title": "貢獻", "text": "t4", "citations": [0]},'
        '{"key": "limitations", "title": "限制", "text": "t5", "citations": [0]}'
        "]}\n"
        "```\n"
        "以上。"
    )


class TestGenerateDigestUsesChatOnce:
    async def test_calls_chat_once_with_expected_args_and_max_tokens(
        self, wire_digest_db, monkeypatch, setup_test_document
    ):
        doc_id, _chunk_id = setup_test_document
        captured_call: dict = {}

        async def fake_chat_once(system, user_content, *, max_tokens=3000, deps=None):
            captured_call.update(
                system=system, user_content=user_content, max_tokens=max_tokens, deps=deps
            )
            return _fenced_json_reply(), {"prompt_tokens": 111, "completion_tokens": 222}

        monkeypatch.setattr(digest_mod, "chat_once", fake_chat_once)

        captured_digest: dict = {}

        async def fake_update_document_digest(session, doc_id_arg, digest, usage):
            captured_digest.update(doc_id=doc_id_arg, digest=digest, usage=usage)

        monkeypatch.setattr(repo, "update_document_digest", fake_update_document_digest)

        await generate_digest(doc_id)

        assert captured_call["max_tokens"] == 3000
        assert "文獻標題：Test Document" in captured_call["user_content"]
        assert captured_call["deps"] is None  # digest 不帶自訂 deps，chat_once 用預設值
        assert captured_digest["doc_id"] == doc_id
        assert captured_digest["usage"] == {"prompt_tokens": 111, "completion_tokens": 222}

    async def test_extract_json_and_validate_survive_fenced_reply_via_chat_once(
        self, wire_digest_db, monkeypatch, setup_test_document
    ):
        """CLAUDE.md 鐵律 1 守門：extract_json/_validate 一字不動——```json 圍欄樣本
        經 chat_once 管線後仍要正確解析並把 citations 解析回含 page/bbox 的完整引用物件。"""
        doc_id, chunk_id = setup_test_document

        async def fake_chat_once(system, user_content, *, max_tokens=3000, deps=None):
            return _fenced_json_reply(), {"prompt_tokens": 10, "completion_tokens": 20}

        monkeypatch.setattr(digest_mod, "chat_once", fake_chat_once)

        captured_digest: dict = {}

        async def fake_update_document_digest(session, doc_id_arg, digest, usage):
            captured_digest.update(doc_id=doc_id_arg, digest=digest, usage=usage)

        monkeypatch.setattr(repo, "update_document_digest", fake_update_document_digest)

        await generate_digest(doc_id)

        digest = captured_digest["digest"]
        assert digest["tldr"] == "一句話總結"
        sections = {s["key"]: s for s in digest["sections"]}
        assert set(sections) == {
            "research_question",
            "method",
            "findings",
            "contributions",
            "limitations",
        }
        rq_citation = sections["research_question"]["citations"][0]
        assert rq_citation["chunk_id"] == chunk_id
        assert rq_citation["chunk_index"] == 0
        assert rq_citation["page"] == 1
        assert rq_citation["bbox_list"] == [[0, 0, 100, 50]]

    async def test_chat_once_error_leaves_digest_null_not_raise(
        self, wire_digest_db, monkeypatch, setup_test_document
    ):
        """digest 失敗只記 log、不擋 ready（既有行為，chat_once 換血後仍需維持）。"""
        doc_id, _chunk_id = setup_test_document

        async def failing_chat_once(system, user_content, *, max_tokens=3000, deps=None):
            raise RuntimeError("boom")

        monkeypatch.setattr(digest_mod, "chat_once", failing_chat_once)
        calls: list = []
        monkeypatch.setattr(
            repo,
            "update_document_digest",
            lambda *a, **k: calls.append((a, k)),
        )

        await generate_digest(doc_id)  # 不應拋出

        assert calls == []


class TestSelectChunksUnaffected:
    """確保這次改動沒有動到 chunk 篩選/截斷邏輯（範圍紀律）。"""

    def test_under_budget_returns_all_untruncated(self):
        chunks = [{"id": i, "content": "x" * 10} for i in range(5)]
        selected, truncated = _select_chunks(chunks)
        assert selected == chunks
        assert truncated is False
