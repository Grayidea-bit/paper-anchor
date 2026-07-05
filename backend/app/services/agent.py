"""對話管線的 agent 環境（Pydantic AI）。

職責：把 Pydantic AI 的事件流映射成本專案的事件協定
（token / reasoning / tool / context_chunks / usage），供 router 轉 SSE。
供應商設定沿用 llm._chat_config()（settings 覆蓋 .env）。

安全底線：無啟用工具時不帶 toolsets——與純串流管線行為一致。
降級保險：帶工具的請求在「尚未輸出任何內容」時收到 4xx → 剝除工具重試
（防供應商/模型不支援 function calling）。
"""

import logging
from collections.abc import AsyncIterator

from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import UsageLimits

from app import settings_store
from app import tools as tools_pkg
from app.llm import (
    LLMError,
    ThinkFilter,
    _backoff,
    _chat_config,
    _default_chat_model,
    _is_retryable,
    _record_request,
)
from app.models_catalog import CLAUDE_MODELS
from app.tools import ToolDeps

logger = logging.getLogger(__name__)

HISTORY_LIMIT = 10
# request_limit 含首輪：5 = 最多 4 輪工具往返後強制收斂
_USAGE_LIMITS = UsageLimits(request_limit=5)
_MAX_ATTEMPTS = 3


def _to_history(history: list[dict]) -> list:
    """DB 訊息 → Pydantic AI message_history（空訊息不進 prompt）。"""
    messages: list = []
    for m in [m for m in history if m["content"].strip()][-HISTORY_LIMIT:]:
        if m["role"] == "user":
            messages.append(ModelRequest(parts=[UserPromptPart(content=m["content"])]))
        else:
            messages.append(ModelResponse(parts=[TextPart(content=m["content"])]))
    return messages


def _build_agent(system: str, with_tools: bool, model_override: str | None = None) -> Agent:
    base_url, api_key, model_name = _chat_config()
    model = OpenAIChatModel(
        model_override or model_name, provider=OpenAIProvider(base_url=base_url, api_key=api_key)
    )
    toolset = tools_pkg.build_toolset() if with_tools else None
    return Agent(
        model,
        instructions=system,
        deps_type=ToolDeps,
        toolsets=[toolset] if toolset else None,
    )


def _resolve_model(backend: str, model: str | None) -> str:
    """送出前允許清單校驗（防任意 model 注入）：不在清單（或未選）→ 回落該來源預設。"""
    if backend == "claude-sdk":
        allowed = CLAUDE_MODELS
        default = CLAUDE_MODELS[0]
    else:
        configured = settings_store.runtime("llm_chat_models")
        allowed = configured if isinstance(configured, list) and configured else None
        default = allowed[0] if allowed else _default_chat_model()
        if allowed is None:
            allowed = [default]
    if model in allowed:
        return model
    return default


async def stream_chat(
    system: str,
    history: list[dict],
    user_content: str,
    deps: ToolDeps,
    model: str | None = None,
) -> AsyncIterator[dict]:
    """事件流：token* / reasoning* / tool* / context_chunks* → usage（最後一次）。"""
    backend = settings_store.runtime("chat_backend") or "openai"
    final_model = _resolve_model(backend, model)

    # M8：後端分派（此處為唯一入口）。claude-sdk → 委派 claude_backend（同一事件協定）。
    if backend == "claude-sdk":
        from app.services import claude_backend

        async for ev in claude_backend.stream_chat(
            system, history, user_content, deps, model=final_model
        ):
            yield ev
        return

    message_history = _to_history(history)
    with_tools = tools_pkg.build_toolset() is not None
    for attempt in range(_MAX_ATTEMPTS):
        think = ThinkFilter()
        visible = False  # 已對外輸出（token）→ 不可重試
        try:
            agent = _build_agent(system, with_tools, final_model)
            async with agent.run_stream_events(
                user_content,
                deps=deps,
                message_history=message_history or None,
                usage_limits=_USAGE_LIMITS,
            ) as stream:
                async for event in stream:
                    name = type(event).__name__
                    if name == "PartStartEvent":
                        # 新 part 的初始內容在 start 事件裡（首 token 常在此，不可漏）
                        part = event.part
                        part_type = type(part).__name__
                        content = getattr(part, "content", None)
                        if part_type == "TextPart" and content:
                            cleaned = think.feed(content)
                            if cleaned:
                                visible = True
                                yield {"type": "token", "text": cleaned}
                        elif part_type == "ThinkingPart" and content:
                            yield {"type": "reasoning", "text": content}
                    elif name == "PartDeltaEvent":
                        delta = event.delta
                        delta_type = type(delta).__name__
                        if delta_type == "TextPartDelta":
                            cleaned = think.feed(delta.content_delta or "")
                            if cleaned:
                                visible = True
                                yield {"type": "token", "text": cleaned}
                        elif delta_type == "ThinkingPartDelta" and delta.content_delta:
                            yield {"type": "reasoning", "text": delta.content_delta}
                    elif name == "FunctionToolCallEvent":
                        yield {"type": "tool", "name": event.part.tool_name, "status": "start"}
                    elif name == "FunctionToolResultEvent":
                        part = getattr(event, "part", None) or event.result
                        status = "error" if type(part).__name__ == "RetryPromptPart" else "done"
                        yield {
                            "type": "tool",
                            "name": getattr(part, "tool_name", "?"),
                            "status": status,
                        }
                        metadata = getattr(part, "metadata", None)
                        if isinstance(metadata, dict) and metadata.get("chunks"):
                            yield {"type": "context_chunks", "chunks": metadata["chunks"]}
                    elif name == "AgentRunResultEvent":
                        usage = event.result.usage
                        # RPM 統計：以實際請求數回填（工具多輪各算一次）
                        for _ in range(max(1, usage.requests)):
                            _record_request()
                        yield {
                            "type": "usage",
                            "prompt_tokens": usage.input_tokens or 0,
                            "completion_tokens": usage.output_tokens or 0,
                        }
            if tail := think.flush():
                yield {"type": "token", "text": tail}
            return
        except Exception as e:  # noqa: BLE001
            message = str(e)
            logger.warning("agent run failed (attempt %s): %s", attempt + 1, message[:300])
            if visible or attempt == _MAX_ATTEMPTS - 1:
                raise LLMError(f"agent 執行失敗：{message[:300]}") from e
            if with_tools and ("400" in message or "tool" in message.lower()):
                # 供應商可能不支援 tools → 剝除工具重試（docs/02 D7 降級保險）
                logger.warning("degrading to no-tools mode")
                with_tools = False
                continue
            if _is_retryable(message):
                await _backoff(attempt)
                continue
            raise LLMError(f"agent 執行失敗：{message[:300]}") from e
