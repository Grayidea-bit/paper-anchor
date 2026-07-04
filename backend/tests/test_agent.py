"""agent 管線測試：用 Pydantic AI 原生 TestModel/FunctionModel，不打網路。"""

import pytest
from pydantic_ai import ToolReturn
from pydantic_ai.messages import ModelRequest, ModelResponse
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from pydantic_ai.models.test import TestModel

from app.services import agent as agent_mod
from app.services.agent import _to_history, stream_chat
from app.tools import ToolDeps

DEPS = ToolDeps(scope="document", doc_id=1, project_id=None)


async def collect(system="sys", history=None, question="q", deps=DEPS):
    events = []
    async for event in stream_chat(system, history or [], question, deps):
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
        def build(system: str, with_tools: bool):
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

        def build(system: str, with_tools: bool):
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


# 確保未用到的 import 不被 lint 認為多餘
_ = DeltaToolCall
