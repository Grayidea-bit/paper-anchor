"""claude_backend.py 測試：事件映射、side-channel、安全組態、錯誤路徑。

不打真 API：monkeypatch claude_agent_sdk 模組本身的 query/訊息類別
（claude_backend.py 內部用 `from claude_agent_sdk import ...` 局部匯入，
呼叫當下才解析屬性，所以 patch 模組屬性即可攔截）。
"""

import claude_agent_sdk as sdk
import pytest

from app import settings_store
from app.llm import LLMError
from app.services import claude_auth, claude_backend
from app.tools import ToolDeps

DEPS = ToolDeps(scope="document", doc_id=1, project_id=None)

# 真實 ensure_token（autouse _token fixture 會把模組屬性換成假的，這裡先留存原函式）
_REAL_ENSURE_TOKEN = claude_auth.ensure_token


def _stream_event(event: dict) -> sdk.StreamEvent:
    return sdk.StreamEvent(uuid="u1", session_id="s1", event=event)


def _text_delta(text: str) -> sdk.StreamEvent:
    return _stream_event(
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}}
    )


def _thinking_delta(text: str) -> sdk.StreamEvent:
    return _stream_event(
        {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": text}}
    )


def _assistant_tool_use(tool_use_id: str, name: str) -> sdk.AssistantMessage:
    return sdk.AssistantMessage(
        content=[sdk.ToolUseBlock(id=tool_use_id, name=name, input={})],
        model="claude-sonnet",
    )


def _tool_result(tool_use_id: str, *, is_error: bool = False) -> sdk.UserMessage:
    return sdk.UserMessage(
        content=[sdk.ToolResultBlock(tool_use_id=tool_use_id, content="ok", is_error=is_error)]
    )


def _result_message(
    *, subtype: str = "success", usage: dict | None = None, api_error_status=None
) -> sdk.ResultMessage:
    return sdk.ResultMessage(
        subtype=subtype,
        duration_ms=10,
        duration_api_ms=10,
        is_error=subtype != "success",
        num_turns=1,
        session_id="s1",
        usage=usage or {"input_tokens": 12, "output_tokens": 34},
        api_error_status=api_error_status,
    )


def _fake_query(messages: list):
    """回一個符合 sdk.query 簽名的 async generator 工廠。"""

    async def _query(*, prompt, options=None, transport=None):
        for m in messages:
            yield m

    return _query


@pytest.fixture(autouse=True)
def _settings_cache(monkeypatch):
    """settings_store.runtime 需要 _cache 已載入（claude_model 走預設 "sonnet"）。"""
    monkeypatch.setattr(settings_store, "_cache", {})
    return None


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    """預設有效 token，個別測試可覆蓋。"""

    async def fake_ensure_token():
        return "fake-oauth-token"

    monkeypatch.setattr(claude_auth, "ensure_token", fake_ensure_token)


@pytest.fixture
def no_tools(monkeypatch):
    """停用工具側信道（不進真的 build_sdk_mcp_server）：build_sdk_mcp_server 回 None。"""
    monkeypatch.setattr(claude_backend.tools_pkg, "build_sdk_mcp_server", lambda deps, sink: None)


async def collect(monkeypatch, messages, *, history=None, question="q", deps=DEPS, model=None):
    monkeypatch.setattr(sdk, "query", _fake_query(messages))
    events = []
    async for event in claude_backend.stream_chat(
        "sys", history or [], question, deps, model=model
    ):
        events.append(event)
    return events


class TestEventMapping:
    """StreamEvent(text/thinking) -> AssistantMessage(ToolUse) -> UserMessage(ToolResult)

    -> ResultMessage 的完整事件映射順序與內容。
    """

    async def test_full_event_sequence(self, monkeypatch, no_tools):
        messages = [
            _thinking_delta("想一下"),
            _text_delta("答案"),
            _text_delta("是 42"),
            _assistant_tool_use("tu1", "mcp__anchor__keyword_search"),
            _tool_result("tu1"),
            _result_message(),
        ]
        events = await collect(monkeypatch, messages)
        types = [e["type"] for e in events]
        assert types == ["reasoning", "token", "token", "tool", "tool", "usage"]

        assert events[0] == {"type": "reasoning", "text": "想一下"}
        assert events[1] == {"type": "token", "text": "答案"}
        assert events[2] == {"type": "token", "text": "是 42"}
        assert events[3] == {"type": "tool", "name": "keyword_search", "status": "start"}
        assert events[4] == {"type": "tool", "name": "keyword_search", "status": "done"}
        assert events[5] == {
            "type": "usage",
            "prompt_tokens": 12,
            "completion_tokens": 34,
        }

    async def test_tool_error_status(self, monkeypatch, no_tools):
        messages = [
            _assistant_tool_use("tu1", "mcp__anchor__keyword_search"),
            _tool_result("tu1", is_error=True),
            _result_message(),
        ]
        events = await collect(monkeypatch, messages)
        tool_events = [e for e in events if e["type"] == "tool"]
        assert tool_events[-1] == {"type": "tool", "name": "keyword_search", "status": "error"}

    async def test_unknown_tool_use_id_maps_to_placeholder(self, monkeypatch, no_tools):
        messages = [_tool_result("missing"), _result_message()]
        events = await collect(monkeypatch, messages)
        assert events[0] == {"type": "tool", "name": "?", "status": "done"}

    async def test_non_text_delta_ignored(self, monkeypatch, no_tools):
        messages = [
            _stream_event({"type": "content_block_delta", "delta": {"type": "signature_delta"}}),
            _stream_event({"type": "content_block_start"}),
            _result_message(),
        ]
        events = await collect(monkeypatch, messages)
        assert [e["type"] for e in events] == ["usage"]

    async def test_rate_limit_event_ignored(self, monkeypatch, no_tools):
        rate_limit_info = type("RLI", (), {})()
        messages = [
            sdk.RateLimitEvent(rate_limit_info=rate_limit_info, uuid="u1", session_id="s1"),
            _result_message(),
        ]
        events = await collect(monkeypatch, messages)
        assert [e["type"] for e in events] == ["usage"]


class TestContextChunksSideChannel:
    """假工具經 build_sdk_mcp_server 的 sink 注入 chunks -> context_chunks 事件。"""

    async def test_sink_flushed_after_tool_done(self, monkeypatch):
        captured_sink: list = []

        def fake_build_sdk_mcp_server(deps, sink):
            captured_sink.append(sink)
            sink.append({"id": 99, "page": 3})
            return None  # server 本身內容無關緊要，這裡只驗證 sink 被填充後吐事件

        monkeypatch.setattr(
            claude_backend.tools_pkg, "build_sdk_mcp_server", fake_build_sdk_mcp_server
        )
        messages = [
            _assistant_tool_use("tu1", "mcp__anchor__keyword_search"),
            _tool_result("tu1"),
            _result_message(),
        ]
        events = await collect(monkeypatch, messages)
        ctx_events = [e for e in events if e["type"] == "context_chunks"]
        assert len(ctx_events) == 1
        assert ctx_events[0]["chunks"] == [{"id": 99, "page": 3}]

    async def test_no_chunks_no_event(self, monkeypatch, no_tools):
        messages = [
            _assistant_tool_use("tu1", "mcp__anchor__keyword_search"),
            _tool_result("tu1"),
            _result_message(),
        ]
        events = await collect(monkeypatch, messages)
        assert not [e for e in events if e["type"] == "context_chunks"]


class TestBuildOptionsSecurity:
    """_build_options 產出的 ClaudeAgentOptions 必須鎖死內建工具與宿主設定讀取。"""

    def test_no_server_no_mcp_fields(self):
        options = claude_backend._build_options("sys", None, {"CLAUDE_CODE_OAUTH_TOKEN": "t"})
        assert options.tools == []
        assert options.setting_sources == []
        assert options.mcp_servers == {}
        assert options.allowed_tools == []

    def test_with_server_allowed_tools_scoped_to_anchor(self):
        fake_server = object()
        options = claude_backend._build_options(
            "sys", fake_server, {"CLAUDE_CODE_OAUTH_TOKEN": "t"}
        )
        assert options.tools == []
        assert options.setting_sources == []
        assert options.mcp_servers == {"anchor": fake_server}
        assert options.allowed_tools == ["mcp__anchor__*"]
        assert all(t.startswith("mcp__anchor__") for t in options.allowed_tools)

    def test_system_prompt_is_plain_string(self):
        options = claude_backend._build_options("純字串系統提示", None, {})
        assert options.system_prompt == "純字串系統提示"

    def test_env_passed_through(self):
        env = {"CLAUDE_CODE_OAUTH_TOKEN": "abc", "CLAUDE_CONFIG_DIR": "/tmp/x"}
        options = claude_backend._build_options("sys", None, env)
        assert options.env == env

    def test_model_defaults_to_sonnet(self, monkeypatch):
        monkeypatch.setattr(settings_store, "_cache", {})
        options = claude_backend._build_options("sys", None, {})
        assert options.model == "sonnet"

    def test_model_reads_settings_override(self, monkeypatch):
        monkeypatch.setattr(settings_store, "_cache", {"claude_model": "opus"})
        options = claude_backend._build_options("sys", None, {})
        assert options.model == "opus"

    def test_model_param_overrides_settings(self, monkeypatch):
        # M9：對話持久化選用模型優先於 settings claude_model
        monkeypatch.setattr(settings_store, "_cache", {"claude_model": "opus"})
        options = claude_backend._build_options("sys", None, {}, "claude-haiku-4-5")
        assert options.model == "claude-haiku-4-5"


class TestStreamChatModelPassthrough:
    """M9：stream_chat(model=...) 透傳到 _build_options（每對話持久化模型）。"""

    async def test_model_passed_to_build_options(self, monkeypatch, no_tools):
        captured: dict = {}
        real_build_options = claude_backend._build_options

        def spy_build_options(system, server, env, model=None):
            captured["model"] = model
            return real_build_options(system, server, env, model)

        monkeypatch.setattr(claude_backend, "_build_options", spy_build_options)
        await collect(monkeypatch, [_result_message()], model="claude-opus-4-8")
        assert captured["model"] == "claude-opus-4-8"

    async def test_no_model_falls_back_to_settings(self, monkeypatch, no_tools):
        captured: dict = {}
        real_build_options = claude_backend._build_options

        def spy_build_options(system, server, env, model=None):
            captured["model"] = model
            return real_build_options(system, server, env, model)

        monkeypatch.setattr(claude_backend, "_build_options", spy_build_options)
        await collect(monkeypatch, [_result_message()])
        assert captured["model"] is None


class TestErrorPaths:
    async def test_missing_token_raises_llm_error(self, monkeypatch, no_tools):
        async def fake_ensure_token():
            raise LLMError("Claude 訂閱未登入，請到設定頁以 Claude 登入或貼入 setup-token")

        monkeypatch.setattr(claude_auth, "ensure_token", fake_ensure_token)
        with pytest.raises(LLMError, match="未登入"):
            await collect(monkeypatch, [])

    async def test_result_subtype_error_raises_llm_error(self, monkeypatch, no_tools):
        messages = [_result_message(subtype="error_max_turns", api_error_status=None)]
        with pytest.raises(LLMError, match="error_max_turns"):
            await collect(monkeypatch, messages)

    async def test_max_turns_with_visible_output_finishes_gracefully(self, monkeypatch, no_tools):
        """error_max_turns 但答案已串流（visible）→ 不 raise、照常回 usage（M15 修正）。

        修正前：raise 會把已完整輸出的回答打成 SSE error、不入庫，使用者重試再燒一輪額度。
        """
        messages = [
            _text_delta("答案本體"),
            _result_message(
                subtype="error_max_turns", usage={"input_tokens": 5, "output_tokens": 9}
            ),
        ]
        events = await collect(monkeypatch, messages)
        kinds = [e["type"] for e in events]
        assert "token" in kinds
        usage = next(e for e in events if e["type"] == "usage")
        assert usage["prompt_tokens"] == 5
        assert usage["completion_tokens"] == 9

    async def test_max_turns_without_output_raises_actionable_message(self, monkeypatch, no_tools):
        """error_max_turns 且無任何可見輸出 → 錯誤訊息要可行動（重試/簡化提問）。"""
        messages = [_result_message(subtype="error_max_turns")]
        with pytest.raises(LLMError, match="重試或簡化提問"):
            await collect(monkeypatch, messages)

    async def test_result_subtype_error_includes_status(self, monkeypatch, no_tools):
        messages = [_result_message(subtype="error_during_execution", api_error_status=529)]
        with pytest.raises(LLMError, match="529"):
            await collect(monkeypatch, messages)

    async def test_assistant_error_authentication_failed(self, monkeypatch, no_tools):
        err = type("Err", (), {"error": "authentication_failed"})()
        assistant = sdk.AssistantMessage(content=[], model="m", error=err)
        with pytest.raises(LLMError, match="未登入或 token 失效"):
            await collect(monkeypatch, [assistant])


class TestTokenAccess:
    """token 取用（官方 setup-token 貼碼流程）：ensure_token / logout。"""

    @pytest.fixture(autouse=True)
    def _clear_settings(self, monkeypatch):
        monkeypatch.setattr(settings_store, "_cache", {})

    async def test_ensure_token_returns_stored_token(self, monkeypatch):
        settings_store._cache["claude_oauth_token"] = "stored-token"
        assert await _REAL_ENSURE_TOKEN() == "stored-token"

    async def test_ensure_token_missing_raises_llm_error(self):
        with pytest.raises(LLMError, match="setup-token"):
            await _REAL_ENSURE_TOKEN()

    async def test_logout_clears_token(self, monkeypatch):
        settings_store._cache["claude_oauth_token"] = "stored-token"
        captured: dict = {}

        async def fake_update(values):
            captured.update(values)
            for k, v in values.items():
                if v == "":
                    settings_store._cache.pop(k, None)
            return dict(settings_store._cache)

        monkeypatch.setattr(settings_store, "update", fake_update)
        await claude_auth.logout()
        assert captured["claude_oauth_token"] == ""
        assert settings_store.runtime("claude_oauth_token") is None
