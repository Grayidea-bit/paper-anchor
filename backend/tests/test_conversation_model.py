"""M9：對話持久化選用模型 — PATCH 端點、send_message 讀 conv.model 傳入、允許清單校驗回落。

不打真 DB / 真 LLM：monkeypatch app.db.repo 的存取函式與 agent.stream_chat。
"""

import httpx
import pytest

from app import settings_store
from app.db import repo
from app.main import app
from app.routers import conversations as conversations_router
from app.services import agent as agent_mod


@pytest.fixture(autouse=True)
def _settings_cache(monkeypatch):
    monkeypatch.setattr(settings_store, "_cache", {})


async def _client():
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def _conv(conv_id=1, *, scope="library", model=None):
    return {
        "id": conv_id,
        "scope": scope,
        "document_id": None,
        "project_id": None,
        "title": "t",
        "model": model,
        "created_at": "2026-01-01T00:00:00Z",
    }


class TestPatchConversationModel:
    async def test_updates_model_and_returns_conversation(self, monkeypatch):
        store: dict = {"model": None}

        async def fake_get_conversation(session, conv_id):
            return _conv(conv_id, model=store["model"])

        async def fake_set_conversation_model(session, conv_id, model):
            store["model"] = model or None

        monkeypatch.setattr(repo, "get_conversation", fake_get_conversation)
        monkeypatch.setattr(repo, "set_conversation_model", fake_set_conversation_model)

        async with await _client() as client:
            resp = await client.patch("/api/conversations/1", json={"model": "claude-opus-4-8"})
        assert resp.status_code == 200
        assert resp.json()["model"] == "claude-opus-4-8"

    async def test_empty_string_clears_to_null(self, monkeypatch):
        store: dict = {"model": "claude-opus-4-8"}

        async def fake_get_conversation(session, conv_id):
            return _conv(conv_id, model=store["model"])

        async def fake_set_conversation_model(session, conv_id, model):
            store["model"] = model or None

        monkeypatch.setattr(repo, "get_conversation", fake_get_conversation)
        monkeypatch.setattr(repo, "set_conversation_model", fake_set_conversation_model)

        async with await _client() as client:
            resp = await client.patch("/api/conversations/1", json={"model": ""})
        assert resp.status_code == 200
        assert resp.json()["model"] is None

    async def test_404_when_conversation_missing(self, monkeypatch):
        async def fake_get_conversation(session, conv_id):
            return None

        monkeypatch.setattr(repo, "get_conversation", fake_get_conversation)

        async with await _client() as client:
            resp = await client.patch("/api/conversations/999", json={"model": "x"})
        assert resp.status_code == 404


class TestSendMessageUsesConversationModel:
    """send_message 載入 conv 後應以 conv.model 呼叫 agent.stream_chat(model=...)。"""

    def _wire_common(self, monkeypatch, *, conv_model):
        async def fake_get_conversation(session, conv_id):
            return _conv(conv_id, model=conv_model)

        async def fake_add_message(session, conv_id, role, content, **kw):
            return {"id": 1, "role": role, "content": content, **kw}

        async def fake_list_messages(session, conv_id):
            return []

        async def fake_embed_query(text):
            return [0.0]

        async def fake_retrieve_context(*a, **kw):
            return []

        monkeypatch.setattr(repo, "get_conversation", fake_get_conversation)
        monkeypatch.setattr(repo, "add_message", fake_add_message)
        monkeypatch.setattr(repo, "list_messages", fake_list_messages)
        monkeypatch.setattr(conversations_router, "embed_query", fake_embed_query)
        monkeypatch.setattr(conversations_router.rag, "retrieve_context", fake_retrieve_context)

    async def test_reads_conv_model_and_passes_to_stream_chat(self, monkeypatch):
        self._wire_common(monkeypatch, conv_model="claude-opus-4-8")
        captured: dict = {}

        async def fake_stream_chat(system, history, user_content, deps, model=None):
            captured["model"] = model
            yield {"type": "token", "text": "答案"}
            yield {"type": "usage", "prompt_tokens": 1, "completion_tokens": 1}

        monkeypatch.setattr(conversations_router.agent, "stream_chat", fake_stream_chat)

        async with await _client() as client:
            async with client.stream(
                "POST", "/api/conversations/1/messages", json={"content": "hi"}
            ) as resp:
                async for _ in resp.aiter_lines():
                    pass
        assert captured["model"] == "claude-opus-4-8"

    async def test_none_model_passed_through_for_backend_fallback(self, monkeypatch):
        """conv.model 為 None → 原樣傳 None，讓 agent.stream_chat 的允許清單校驗接手回落。"""
        self._wire_common(monkeypatch, conv_model=None)
        captured: dict = {}

        async def fake_stream_chat(system, history, user_content, deps, model=None):
            captured["model"] = model
            yield {"type": "token", "text": "答案"}
            yield {"type": "usage", "prompt_tokens": 1, "completion_tokens": 1}

        monkeypatch.setattr(conversations_router.agent, "stream_chat", fake_stream_chat)

        async with await _client() as client:
            async with client.stream(
                "POST", "/api/conversations/1/messages", json={"content": "hi"}
            ) as resp:
                async for _ in resp.aiter_lines():
                    pass
        assert captured["model"] is None


class TestResolveModelFallback:
    """agent._resolve_model：不在允許清單（或 None）→ 回落該來源預設。"""

    def test_openai_backend_falls_back_when_not_configured(self, monkeypatch):
        monkeypatch.setattr(settings_store, "_cache", {})
        assert agent_mod._resolve_model("openai", "some-random-model") != "some-random-model"

    def test_openai_backend_uses_configured_list_first_item_as_default(self, monkeypatch):
        monkeypatch.setattr(settings_store, "_cache", {"llm_chat_models": ["m1", "m2"]})
        assert agent_mod._resolve_model("openai", "not-in-list") == "m1"
        assert agent_mod._resolve_model("openai", "m2") == "m2"
        assert agent_mod._resolve_model("openai", None) == "m1"

    def test_claude_backend_falls_back_to_first_builtin(self, monkeypatch):
        from app.models_catalog import CLAUDE_MODELS

        monkeypatch.setattr(settings_store, "_cache", {})
        assert agent_mod._resolve_model("claude-sdk", "not-a-real-model") == CLAUDE_MODELS[0]
        assert agent_mod._resolve_model("claude-sdk", None) == CLAUDE_MODELS[0]
        assert agent_mod._resolve_model("claude-sdk", CLAUDE_MODELS[1]) == CLAUDE_MODELS[1]
