"""Claude Agent SDK 作為第二個 chat 後端（訂閱額度 / CLAUDE_CODE_OAUTH_TOKEN）。

職責：把 claude-agent-sdk 的訊息/事件流映射成本專案的事件協定
（token / reasoning / tool / context_chunks / usage），與 services/agent.py
完全一致——router/SSE/引用鏈/前端零改動。

安全底線（Phase 0 實測）：tools=[] + setting_sources=[] + allowed_tools 僅我方
MCP 工具 → 模型無任何內建工具。system_prompt 用純字串＝完全取代 Claude Code 提示詞。

認證：token 放 options.env["CLAUDE_CODE_OAUTH_TOKEN"]（實測生效；process env 不動，
且絕不設 ANTHROPIC_API_KEY）。缺 token → LLMError 提示去設定頁登入。

串流：include_partial_messages=True → StreamEvent 逐 token。文字以 StreamEvent 為準；
AssistantMessage 只用來抓 ToolUseBlock 與 error 欄位（partial 模式下它仍整塊到，
文字不可重複吐）。

並發安全：引用鏈側信道用 contextvars.ContextVar 綁 per-request list（同容器多請求不串）。
"""

import contextvars
import logging
import os
from collections.abc import AsyncIterator

from app import settings_store
from app import tools as tools_pkg
from app.llm import LLMError, ThinkFilter, _backoff, _is_retryable, _record_request
from app.services import claude_auth
from app.tools import ToolDeps

logger = logging.getLogger(__name__)

HISTORY_LIMIT = 10
_MAX_ATTEMPTS = 3
# max_turns：模型↔工具的 agentic 迴圈上限。RAG 情境 context 已在 system prompt，
# 但推理模型常額外多輪 keyword_search 佐證；8 實測對複雜提問仍不足（新對話反覆
# error_max_turns），提高到 16 作為「防失控」的硬上限而非常態邊界。
# 搭配下方 error_max_turns 特判：已有可見輸出時優雅收尾，不把完整答案打成錯誤。
_MAX_TURNS = 16
# session 檔/CLAUDE_CONFIG_DIR 進容器暫存目錄（不污染）
_CONFIG_DIR = "/tmp/claude-anchor"
_CWD = "/tmp/claude-anchor"

# per-request 引用鏈側信道（工具轉接器 append，本模組讀取後清空）
_sink_var: contextvars.ContextVar[list] = contextvars.ContextVar("claude_tool_sink")


def _serialize_history(history: list[dict], user_content: str) -> str:
    """歷史揉進 prompt（沿用 agent.py 過濾邏輯：空訊息剔除、尾端 HISTORY_LIMIT）。"""
    kept = [m for m in history if m["content"].strip()][-HISTORY_LIMIT:]
    if not kept:
        return user_content
    lines = []
    for m in kept:
        speaker = "使用者" if m["role"] == "user" else "助手"
        lines.append(f"{speaker}：{m['content']}")
    return "[先前對話]\n" + "\n".join(lines) + "\n[/先前對話]\n\n" + user_content


def _build_options(system: str, server, env: dict, model: str | None = None):
    """組 ClaudeAgentOptions（安全鎖定組合 + token 注入）。

    model：對話持久化的選用模型（M9，經 agent.stream_chat 允許清單校驗後傳入）；
    未指定時沿用舊行為（settings claude_model 覆蓋 > "sonnet" 別名）。
    """
    from claude_agent_sdk import ClaudeAgentOptions

    model = model or settings_store.runtime("claude_model") or "sonnet"
    kwargs = dict(
        system_prompt=system,
        model=model,
        max_turns=_MAX_TURNS,
        include_partial_messages=True,
        # 安全鎖定（Phase 0 實測）：無內建工具、不讀宿主設定
        tools=[],
        setting_sources=[],
        env=env,
        cwd=_CWD,
    )
    if server is not None:
        kwargs["mcp_servers"] = {"anchor": server}
        kwargs["allowed_tools"] = ["mcp__anchor__*"]
    return ClaudeAgentOptions(**kwargs)


def _flush_sink() -> list | None:
    """讀側信道 chunks 並清空（每次 tool done 後呼叫）。"""
    sink = _sink_var.get()
    if not sink:
        return None
    chunks = list(sink)
    sink.clear()
    return chunks


async def stream_chat(
    system: str,
    history: list[dict],
    user_content: str,
    deps: ToolDeps,
    model: str | None = None,
    *,
    with_tools: bool = True,
) -> AsyncIterator[dict]:
    """事件流：token* / reasoning* / tool* / context_chunks* → usage（最後一次）。

    with_tools=False：不建 MCP server（options 無 mcp_servers/allowed_tools），
    與 agent.stream_chat 的 with_tools 語意一致（見 chat_once）。
    """
    from claude_agent_sdk import (
        AssistantMessage,
        RateLimitEvent,
        ResultMessage,
        StreamEvent,
        ToolResultBlock,
        ToolUseBlock,
        UserMessage,
        query,
    )

    token = await claude_auth.ensure_token()  # 缺 token → LLMError
    # SDK 需要 cwd/CONFIG_DIR 實際存在（session 檔落此，容器暫存不污染）
    os.makedirs(_CONFIG_DIR, exist_ok=True)
    os.makedirs(_CWD, exist_ok=True)
    env = {
        "CLAUDE_CODE_OAUTH_TOKEN": token,
        "CLAUDE_CONFIG_DIR": _CONFIG_DIR,
    }
    prompt = _serialize_history(history, user_content)

    for attempt in range(_MAX_ATTEMPTS):
        sink: list = []
        _sink_var.set(sink)
        server = tools_pkg.build_sdk_mcp_server(deps, sink) if with_tools else None
        options = _build_options(system, server, env, model)
        think = ThinkFilter()
        visible = False  # 已對外輸出 token → 不可重試
        tool_names: dict[str, str] = {}  # tool_use_id -> 工具名（ToolResult 只帶 id）
        try:
            async for msg in query(prompt=prompt, options=options):
                # --- StreamEvent：逐 token 文字/思考（文字的唯一真實來源）---
                if isinstance(msg, StreamEvent):
                    ev = msg.event or {}
                    if ev.get("type") != "content_block_delta":
                        continue
                    delta = ev.get("delta") or {}
                    dtype = delta.get("type")
                    if dtype == "text_delta":
                        cleaned = think.feed(delta.get("text") or "")
                        if cleaned:
                            visible = True
                            yield {"type": "token", "text": cleaned}
                    elif dtype == "thinking_delta":
                        thinking = delta.get("thinking") or ""
                        if thinking:
                            yield {"type": "reasoning", "text": thinking}
                    # signature_delta / 其他一律忽略
                    continue

                # --- AssistantMessage：只抓 ToolUseBlock 與 error（文字不重吐）---
                if isinstance(msg, AssistantMessage):
                    if msg.error is not None:
                        err = _assistant_error(msg)
                        raise LLMError(err)
                    for block in msg.content:
                        if isinstance(block, ToolUseBlock):
                            tool_names[block.id] = block.name
                            yield {
                                "type": "tool",
                                "name": _short_tool_name(block.name),
                                "status": "start",
                            }
                    continue

                # --- UserMessage 內的 ToolResultBlock：tool done/error + 側信道 ---
                if isinstance(msg, UserMessage):
                    blocks = msg.content if isinstance(msg.content, list) else []
                    for block in blocks:
                        if not isinstance(block, ToolResultBlock):
                            continue
                        name = _short_tool_name(tool_names.get(block.tool_use_id, "?"))
                        status = "error" if block.is_error else "done"
                        yield {"type": "tool", "name": name, "status": status}
                        chunks = _flush_sink()
                        if chunks:
                            yield {"type": "context_chunks", "chunks": chunks}
                    continue

                # --- RateLimitEvent：可 log，不中斷 ---
                if isinstance(msg, RateLimitEvent):
                    logger.info("claude rate limit event: %s", _rate_limit_repr(msg))
                    continue

                # --- ResultMessage：usage + RPM 回填；subtype!=success → LLMError ---
                if isinstance(msg, ResultMessage):
                    for _ in range(max(1, msg.num_turns)):
                        _record_request()
                    if msg.subtype == "error_max_turns" and visible:
                        # 迴圈在 _MAX_TURNS 觸頂，但答案文字已串流給前端——此時 raise 只會
                        # 把（多半已完整的）回答打成 SSE error、不入庫，逼使用者重試再燒一輪
                        # 額度。改為優雅收尾：記警告、照常回 usage，讓回答正常入庫。
                        logger.warning(
                            "claude backend hit max_turns (%s) after visible output; "
                            "finishing gracefully",
                            _MAX_TURNS,
                        )
                    elif msg.subtype != "success":
                        status = msg.api_error_status
                        extra = f", status={status}" if status else ""
                        if msg.subtype == "error_max_turns":
                            raise LLMError(
                                "回答未在工具呼叫輪次上限內收斂（error_max_turns）："
                                "請重試或簡化提問"
                            )
                        raise LLMError(f"Claude 後端執行失敗（subtype={msg.subtype}{extra}）")
                    usage = msg.usage or {}
                    yield {
                        "type": "usage",
                        "prompt_tokens": usage.get("input_tokens", 0) or 0,
                        "completion_tokens": usage.get("output_tokens", 0) or 0,
                    }
                    continue

                # 未知型別（SystemMessage 等）一律安靜忽略

            if tail := think.flush():
                yield {"type": "token", "text": tail}
            return
        except LLMError:
            raise
        except Exception as e:  # noqa: BLE001
            message = str(e)
            logger.warning("claude backend run failed (attempt %s): %s", attempt + 1, message[:300])
            if visible or attempt == _MAX_ATTEMPTS - 1:
                raise LLMError(f"Claude 後端執行失敗：{message[:300]}") from e
            if _is_retryable(message):
                await _backoff(attempt)
                continue
            raise LLMError(f"Claude 後端執行失敗：{message[:300]}") from e


def _short_tool_name(name: str) -> str:
    """mcp__anchor__keyword_search → keyword_search（前端顯示與 openai 後端一致）。"""
    if name.startswith("mcp__"):
        return name.rsplit("__", 1)[-1]
    return name


def _assistant_error(msg) -> str:
    """AssistantMessage.error → 明確中文錯誤（認證失敗特別處理）。"""
    err = msg.error
    err_type = getattr(err, "error", None) or getattr(err, "type", None) or str(err)
    if err_type == "authentication_failed":
        return "Claude 訂閱未登入或 token 失效，請到設定頁重新登入"
    return f"Claude 後端錯誤：{err_type}"


def _rate_limit_repr(msg) -> str:
    for attr in ("status", "rate_limit", "info"):
        val = getattr(msg, attr, None)
        if val is not None:
            return f"{attr}={val}"
    return repr(msg)[:200]
