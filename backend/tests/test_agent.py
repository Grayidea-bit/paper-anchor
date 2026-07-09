"""agent 管線測試：用 Pydantic AI 原生 TestModel/FunctionModel，不打網路。"""

import pytest
from pydantic_ai import ToolReturn
from pydantic_ai.messages import ModelRequest, ModelResponse
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from pydantic_ai.models.test import TestModel

from app.services import agent as agent_mod
from app.services.agent import _to_history, chat_once, stream_chat
from app.tools import ToolDeps

DEPS = ToolDeps(scope="document", doc_id=1, project_id=None)


async def collect(system="sys", history=None, question="q", deps=DEPS, **kwargs):
    events = []
    async for event in stream_chat(system, history or [], question, deps, **kwargs):
        events.append(event)
    return events


class TestToHistory:
    def test_roles_mapped(self):
        history = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "assistant", "content": "   "},  # 空訊息剔除
        ]
        msgs = _to_history(history)
        assert len(msgs) == 2
        assert isinstance(msgs[0], ModelRequest)
        assert isinstance(msgs[1], ModelResponse)

    def test_limit(self):
        history = [{"role": "user", "content": f"m{i}"} for i in range(30)]
        assert len(_to_history(history)) == agent_mod.HISTORY_LIMIT


class TestStreamChatWithTestModel:
    """TestModel 會自動呼叫每個註冊工具一次 → 驗證完整事件鏈。"""

    @pytest.fixture
    def fake_toolset(self, monkeypatch):
        from pydantic_ai.toolsets import FunctionToolset

        async def fake_lookup(query: str) -> ToolReturn:
            """查資料。"""
            return ToolReturn(
                return_value=f"[C99] 假段落（{query}）",
                metadata={"chunks": [{"id": 99, "page": 3}]},
            )

        toolset = FunctionToolset(tools=[fake_lookup])
        monkeypatch.setattr(agent_mod.tools_pkg, "build_toolset", lambda: toolset)
        return toolset

    @pytest.fixture
    def use_test_model(self, monkeypatch):
        def build(
            system: str,
            with_tools: bool,
            model_override: str | None = None,
            max_tokens: int | None = None,
        ):
            from pydantic_ai import Agent

            toolset = agent_mod.tools_pkg.build_toolset() if with_tools else None
            return Agent(
                TestModel(),
                instructions=system,
                deps_type=ToolDeps,
                toolsets=[toolset] if toolset else None,
            )

        monkeypatch.setattr(agent_mod, "_build_agent", build)

    async def test_tool_events_and_context_chunks(self, fake_toolset, use_test_model):
        events = await collect()
        types = [e["type"] for e in events]
        assert "tool" in types
        tool_events = [e for e in events if e["type"] == "tool"]
        assert {e["status"] for e in tool_events} >= {"start", "done"}
        ctx_events = [e for e in events if e["type"] == "context_chunks"]
        assert ctx_events and ctx_events[0]["chunks"][0]["id"] == 99
        assert types[-1] == "usage"
        assert any(e["type"] == "token" for e in events)

    async def test_no_toolset_plain_stream(self, use_test_model, monkeypatch):
        monkeypatch.setattr(agent_mod.tools_pkg, "build_toolset", lambda: None)
        events = await collect()
        types = {e["type"] for e in events}
        assert "tool" not in types
        assert "token" in types and "usage" in types

    async def test_with_tools_false_skips_toolset_even_when_available(
        self, fake_toolset, use_test_model
    ):
        """T-DG-01：with_tools=False（digest 情境）不掛工具，即便工具本身可用。"""
        events = await collect(with_tools=False)
        types = {e["type"] for e in events}
        assert "tool" not in types
        assert "context_chunks" not in types
        assert "token" in types and "usage" in types


class TestBuildAgentToolsAndMaxTokens:
    """_build_agent 的 with_tools / max_tokens 傳遞路徑（openai 路徑，不打網路）。"""

    @pytest.fixture(autouse=True)
    def _fake_chat_config(self, monkeypatch):
        monkeypatch.setattr(agent_mod, "_chat_config", lambda: ("http://x", "key", "model"))

    @pytest.fixture
    def spy_agent(self, monkeypatch):
        from pydantic_ai import Agent as RealAgent

        captured: dict = {}

        class SpyAgent(RealAgent):
            def __init__(self, *args, **kwargs):
                captured.update(kwargs)
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(agent_mod, "Agent", SpyAgent)
        return captured

    def test_with_tools_false_passes_no_toolsets(self, spy_agent, monkeypatch):
        from pydantic_ai.toolsets import FunctionToolset

        async def dummy(query: str) -> str:
            """dummy"""
            return "x"

        monkeypatch.setattr(
            agent_mod.tools_pkg, "build_toolset", lambda: FunctionToolset(tools=[dummy])
        )
        agent_mod._build_agent("sys", False)
        assert spy_agent["toolsets"] is None

    def test_with_tools_true_passes_toolsets(self, spy_agent, monkeypatch):
        from pydantic_ai.toolsets import FunctionToolset

        async def dummy(query: str) -> str:
            """dummy"""
            return "x"

        toolset = FunctionToolset(tools=[dummy])
        monkeypatch.setattr(agent_mod.tools_pkg, "build_toolset", lambda: toolset)
        agent_mod._build_agent("sys", True)
        assert spy_agent["toolsets"] == [toolset]

    def test_max_tokens_sets_model_settings(self, spy_agent):
        agent_mod._build_agent("sys", False, max_tokens=500)
        assert spy_agent["model_settings"] == {"max_tokens": 500}

    def test_no_max_tokens_leaves_model_settings_none(self, spy_agent):
        agent_mod._build_agent("sys", False)
        assert spy_agent["model_settings"] is None


class TestDegradeOn400:
    """帶 tools 收到 400 且未輸出 → 剝除工具重試。"""

    async def test_degrades_then_succeeds(self, monkeypatch):
        from pydantic_ai import Agent
        from pydantic_ai.toolsets import FunctionToolset

        async def dummy(query: str) -> str:
            """dummy"""
            return "x"

        monkeypatch.setattr(
            agent_mod.tools_pkg, "build_toolset", lambda: FunctionToolset(tools=[dummy])
        )

        async def flaky_stream(messages, info: AgentInfo):
            if info.function_tools:  # 模擬供應商不支援 tools
                raise RuntimeError("Error code: 400 - tools not supported")
            yield "ok "
            yield "done"

        def build(
            system: str,
            with_tools: bool,
            model_override: str | None = None,
            max_tokens: int | None = None,
        ):
            toolset = agent_mod.tools_pkg.build_toolset() if with_tools else None
            return Agent(
                FunctionModel(stream_function=flaky_stream),
                instructions=system,
                deps_type=ToolDeps,
                toolsets=[toolset] if toolset else None,
            )

        monkeypatch.setattr(agent_mod, "_build_agent", build)
        events = await collect()
        text = "".join(e["text"] for e in events if e["type"] == "token")
        assert text == "ok done"


class TestChatOnce:
    """T-DG-01：非串流單輪對話（digest 用）——消費 stream_chat 事件流。"""

    async def test_accumulates_only_token_text_and_usage_shape(self, monkeypatch):
        async def fake_stream_chat(
            system, history, user_content, deps, model=None, *, with_tools=True, max_tokens=None
        ):
            yield {"type": "reasoning", "text": "想一下"}
            yield {"type": "token", "text": "答案"}
            yield {"type": "tool", "name": "x", "status": "start"}
            yield {"type": "token", "text": "續集"}
            yield {"type": "usage", "prompt_tokens": 12, "completion_tokens": 34}

        monkeypatch.setattr(agent_mod, "stream_chat", fake_stream_chat)
        text, usage = await chat_once("sys", "user content")
        assert text == "答案續集"  # 只拼 token，reasoning/tool 事件不進文字
        assert usage == {"prompt_tokens": 12, "completion_tokens": 34}

    async def test_default_usage_when_no_usage_event(self, monkeypatch):
        async def fake_stream_chat(*args, **kwargs):
            yield {"type": "token", "text": "hi"}

        monkeypatch.setattr(agent_mod, "stream_chat", fake_stream_chat)
        text, usage = await chat_once("sys", "user")
        assert text == "hi"
        assert usage == {"prompt_tokens": 0, "completion_tokens": 0}

    async def test_with_tools_false_and_max_tokens_forwarded(self, monkeypatch):
        captured: dict = {}

        async def fake_stream_chat(
            system, history, user_content, deps, model=None, *, with_tools=True, max_tokens=None
        ):
            captured.update(
                system=system,
                history=history,
                user_content=user_content,
                deps=deps,
                with_tools=with_tools,
                max_tokens=max_tokens,
            )
            yield {"type": "usage", "prompt_tokens": 1, "completion_tokens": 2}

        monkeypatch.setattr(agent_mod, "stream_chat", fake_stream_chat)
        custom_deps = ToolDeps(scope="project", doc_id=None, project_id=7)
        await chat_once("sys", "hello", max_tokens=999, deps=custom_deps)
        assert captured == {
            "system": "sys",
            "history": [],
            "user_content": "hello",
            "deps": custom_deps,
            "with_tools": False,
            "max_tokens": 999,
        }

    async def test_default_deps_used_when_none(self, monkeypatch):
        captured: dict = {}

        async def fake_stream_chat(
            system, history, user_content, deps, model=None, *, with_tools=True, max_tokens=None
        ):
            captured["deps"] = deps
            yield {"type": "usage", "prompt_tokens": 0, "completion_tokens": 0}

        monkeypatch.setattr(agent_mod, "stream_chat", fake_stream_chat)
        await chat_once("sys", "hello")
        assert captured["deps"] == agent_mod._DEFAULT_ONCE_DEPS

    async def test_claude_style_fenced_json_reply_survives_extract_json(self, monkeypatch):
        """chat_once 只拼 token 文字（reasoning/工具事件不混入）——Claude 後端典型回覆
        帶 ```json 圍欄，extract_json 仍要能解析（CLAUDE.md 鐵律 1 守門）。"""
        from app.llm import extract_json

        reply = (
            "```json\n"
            '{"tldr": "一句話總結", "sections": ['
            '{"key": "research_question", "title": "研究問題", "text": "...", '
            '"citations": [1]}]}\n'
            "```"
        )

        async def fake_stream_chat(
            system, history, user_content, deps, model=None, *, with_tools=True, max_tokens=None
        ):
            yield {"type": "reasoning", "text": "內部思考不應混入文字"}
            for ch in reply:
                yield {"type": "token", "text": ch}
            yield {"type": "usage", "prompt_tokens": 100, "completion_tokens": 50}

        monkeypatch.setattr(agent_mod, "stream_chat", fake_stream_chat)
        text, usage = await chat_once("sys", "content")
        assert text == reply
        parsed = extract_json(text)
        assert parsed["tldr"] == "一句話總結"
        assert usage == {"prompt_tokens": 100, "completion_tokens": 50}


# 確保未用到的 import 不被 lint 認為多餘
_ = DeltaToolCall
