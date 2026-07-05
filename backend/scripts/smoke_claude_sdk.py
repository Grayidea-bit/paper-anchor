"""煙霧測試：claude-agent-sdk 作為第二個 chat 後端（訂閱額度 / CLAUDE_CODE_OAUTH_TOKEN）。

    docker compose exec api python -m scripts.smoke_claude_sdk

實驗性質，不入版控決策前；風格比照 scripts/smoke_pydantic_ai.py。

驗證矩陣（見 plans/ai-db-glittery-sun.md Phase 0）：
  (a) 訂閱 token 認證成功（最簡 query）
  (b) include_partial_messages=True 逐 token 串流（StreamEvent / text_delta / thinking_delta）
  (c) @tool + create_sdk_mcp_server 自訂工具被呼叫 + 側信道（模組級 list）
  (d) 安全鎖定：tools=[] + setting_sources=[] + allowed_tools 僅我方 MCP → 無內建工具
  (e) ResultMessage 欄位 dump（usage / total_cost_usd / session_id / subtype）

Token 來源：環境變數 CLAUDE_CODE_OAUTH_TOKEN
（由使用者在宿主機 `claude setup-token` 產生後貼進容器 env）。
沒有 token → 需認證的項目標 SKIP（待 token），只跑不需認證的靜態檢查。

認證注入以 options.env 為主（計畫指定實測點）：驗證 options.env 注入 token 是否生效
（相對於 process env）。session 檔透過 CLAUDE_CONFIG_DIR=/tmp/claude-smoke 進容器暫存。
"""

import asyncio
import os
import sys

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
    query,
    tool,
)

TOKEN = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
MODEL = os.environ.get("CLAUDE_SMOKE_MODEL", "sonnet")
CONFIG_DIR = "/tmp/claude-smoke"

# 側信道：工具函式 append，主迴圈讀取（驗證引用鏈 chunks 傳遞可行性）
_tool_sidechannel: list[dict] = []


# --- (c) 自訂 MCP 工具：回固定假資料 ---------------------------------------
@tool(
    "lookup_paper",
    "在論文庫中依關鍵字查詢，回傳相關段落與頁碼。回答論文數據前必須先呼叫本工具。",
    {"keyword": str},
)
async def lookup_paper(args: dict) -> dict:
    keyword = args.get("keyword", "")
    # 側信道：模擬 claude_backend 引用鏈——工具把 chunks append 到模組級 list
    _tool_sidechannel.append({"keyword": keyword, "chunk_id": "C1421", "page": 7})
    return {
        "content": [
            {
                "type": "text",
                "text": (
                    f"[C1421]（p.7）KSDD2 資料集上的結果："
                    f"image AUROC 94.24 / pixel PRO 70.67（關鍵字：{keyword}）"
                ),
            }
        ]
    }


def _base_env() -> dict:
    """options.env：注入 token + CLAUDE_CONFIG_DIR（session 檔進暫存）。"""
    env = {"CLAUDE_CONFIG_DIR": CONFIG_DIR}
    if TOKEN:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = TOKEN
    return env


def _summarize_messages(msgs: list) -> dict:
    """統計訊息/事件型別與摘要。"""
    counts: dict[str, int] = {}
    stream_event_types: dict[str, int] = {}
    text_out: list[str] = []
    thinking_out: list[str] = []
    tool_uses: list[str] = []
    builtin_tool_uses: list[str] = []
    result: ResultMessage | None = None
    init_tools: list[str] | None = None
    auth_error: str | None = None

    for m in msgs:
        name = type(m).__name__
        counts[name] = counts.get(name, 0) + 1
        if isinstance(m, SystemMessage):
            if m.subtype == "init" and isinstance(m.data, dict):
                init_tools = m.data.get("tools")
        elif isinstance(m, StreamEvent):
            ev = m.event or {}
            etype = ev.get("type", "?")
            stream_event_types[etype] = stream_event_types.get(etype, 0) + 1
            # content_block_delta 內含 text_delta / thinking_delta
            if etype == "content_block_delta":
                d = ev.get("delta", {})
                dtype = d.get("type", "?")
                key = f"delta:{dtype}"
                stream_event_types[key] = stream_event_types.get(key, 0) + 1
        elif isinstance(m, AssistantMessage):
            if getattr(m, "error", None):
                auth_error = m.error
            for b in m.content:
                if isinstance(b, TextBlock):
                    text_out.append(b.text)
                elif isinstance(b, ThinkingBlock):
                    thinking_out.append(b.thinking)
                elif isinstance(b, ToolUseBlock):
                    tname = b.name
                    tool_uses.append(tname)
                    # 內建工具 = 非 mcp__ 前綴（Bash/Read/... 都算內建）
                    if not tname.startswith("mcp__"):
                        builtin_tool_uses.append(tname)
        elif isinstance(m, ResultMessage):
            result = m

    return {
        "counts": counts,
        "stream_event_types": stream_event_types,
        "text_head": "".join(text_out)[:300],
        "thinking_head": "".join(thinking_out)[:200],
        "tool_uses": tool_uses,
        "builtin_tool_uses": builtin_tool_uses,
        "init_tools": init_tools,
        "auth_error": auth_error,
        "result": result,
    }


async def _run(prompt: str, options: ClaudeAgentOptions) -> tuple[list, str | None]:
    msgs: list = []
    err = None
    try:
        async for m in query(prompt=prompt, options=options):
            msgs.append(m)
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {str(e)[:300]}"
    return msgs, err


def _p(status: str, name: str, detail: str) -> None:
    print(f"[{status:4}] {name}")
    for line in detail.splitlines():
        print(f"        {line}")


# --- (a) 認證成功 -----------------------------------------------------------
async def test_a_auth() -> bool:
    if not TOKEN:
        _p("SKIP", "(a) 訂閱 token 認證", "無 CLAUDE_CODE_OAUTH_TOKEN → 待 token")
        return True
    opts = ClaudeAgentOptions(
        model=MODEL,
        system_prompt="你是簡潔的助手。只回一行。",
        setting_sources=[],
        env=_base_env(),
        cwd="/tmp",
        max_turns=1,
    )
    msgs, err = await _run("回答『pong』，不要多說。", opts)
    s = _summarize_messages(msgs)
    if err or s["auth_error"]:
        _p(
            "FAIL",
            "(a) 訂閱 token 認證",
            f"err={err} auth_error={s['auth_error']}\ncounts={s['counts']}",
        )
        return False
    _p(
        "PASS",
        "(a) 訂閱 token 認證 (options.env 注入)",
        f"answer={s['text_head']!r}\ncounts={s['counts']}",
    )
    return True


# --- options.env vs process env 注入判定 ------------------------------------
async def test_env_injection() -> None:
    """判定 options.env 注入 token 是否生效（相對 process env）。

    做法：把 token 只放進 options.env，同時確保 process env 沒有 token
    （臨時移除），若認證成功 → 證明 options.env 注入生效。
    """
    if not TOKEN:
        _p("SKIP", "options.env 注入判定", "無 token → 待 token")
        return
    saved = {}
    for k in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        if k in os.environ:
            saved[k] = os.environ.pop(k)
    try:
        opts = ClaudeAgentOptions(
            model=MODEL,
            system_prompt="只回一個字。",
            setting_sources=[],
            env=_base_env(),  # token 僅在此
            cwd="/tmp",
            max_turns=1,
        )
        msgs, err = await _run("回 ok", opts)
        s = _summarize_messages(msgs)
        ok = not err and not s["auth_error"]
        _p(
            "PASS" if ok else "FAIL",
            "options.env 注入判定（process env 已清 token）",
            f"認證{'成功→options.env 生效' if ok else '失敗'}；"
            f"err={err} auth_error={s['auth_error']}",
        )
    finally:
        os.environ.update(saved)


# --- (b) 逐 token 串流 ------------------------------------------------------
async def test_b_stream() -> bool:
    if not TOKEN:
        _p("SKIP", "(b) 逐 token 串流", "無 token → 待 token")
        return True
    opts = ClaudeAgentOptions(
        model=MODEL,
        system_prompt="你是助手。用大約三句話說明什麼是快取。",
        setting_sources=[],
        env=_base_env(),
        cwd="/tmp",
        max_turns=1,
        include_partial_messages=True,
    )
    msgs, err = await _run("什麼是 CPU 快取？三句話。", opts)
    s = _summarize_messages(msgs)
    if err or s["auth_error"]:
        _p("FAIL", "(b) 逐 token 串流", f"err={err} auth_error={s['auth_error']}")
        return False
    se = s["stream_event_types"]
    has_stream_event = s["counts"].get("StreamEvent", 0) > 0
    has_text_delta = se.get("delta:text_delta", 0) > 0
    mode = (
        "StreamEvent 逐 token"
        if has_stream_event and has_text_delta
        else "退回逐 block（TextBlock）"
    )
    _p(
        "PASS",
        f"(b) 串流模式：{mode}",
        f"StreamEvent 數={s['counts'].get('StreamEvent', 0)}\n"
        f"stream_event_types={se}\n"
        f"（thinking_delta 存在={'delta:thinking_delta' in se}）\n"
        f"text_head={s['text_head'][:120]!r}",
    )
    return True


# --- (c) 自訂工具被呼叫 + 側信道 -------------------------------------------
async def test_c_tool() -> bool:
    if not TOKEN:
        _p(
            "SKIP",
            "(c) 自訂 MCP 工具 + 側信道",
            "無 token → 待 token（工具/server 建構本身不需認證，已於載入時驗證）",
        )
        # 靜態驗證：server 能建構
        try:
            create_sdk_mcp_server("anchor", "0.0.1", [lookup_paper])
            print("        [靜態] create_sdk_mcp_server 建構成功")
        except Exception as e:  # noqa: BLE001
            print(f"        [靜態] 建構失敗：{e}")
        return True
    _tool_sidechannel.clear()
    server = create_sdk_mcp_server("anchor", "0.0.1", [lookup_paper])
    opts = ClaudeAgentOptions(
        model=MODEL,
        system_prompt=(
            "你是文獻助手。回答論文數據問題前，必須先呼叫 lookup_paper 工具查詢，"
            "不得憑記憶回答。查到後引用工具給的頁碼與 [C####] 標記。"
        ),
        tools=[],
        setting_sources=[],
        mcp_servers={"anchor": server},
        allowed_tools=["mcp__anchor__lookup_paper"],
        env=_base_env(),
        cwd="/tmp",
        max_turns=4,
    )
    msgs, err = await _run("KSDD2 的 image AUROC 是多少？請先查工具。", opts)
    s = _summarize_messages(msgs)
    tool_called = any("lookup_paper" in t for t in s["tool_uses"])
    if err or not tool_called:
        _p(
            "FAIL",
            "(c) 自訂 MCP 工具 + 側信道",
            f"tool_called={tool_called} err={err}\ntool_uses={s['tool_uses']}\n"
            f"sidechannel={_tool_sidechannel}",
        )
        return False
    _p(
        "PASS",
        "(c) 自訂 MCP 工具被呼叫 + 側信道",
        f"tool_uses={s['tool_uses']}\n"
        f"側信道 list（工具 append，主迴圈可讀）={_tool_sidechannel}\n"
        f"answer_head={s['text_head'][:160]!r}",
    )
    return True


# --- (d) 安全鎖定 -----------------------------------------------------------
async def test_d_lockdown() -> bool:
    server = create_sdk_mcp_server("anchor", "0.0.1", [lookup_paper])
    opts = ClaudeAgentOptions(
        model=MODEL,
        system_prompt="你是文獻助手。只能用 lookup_paper 工具。",
        tools=[],
        setting_sources=[],
        mcp_servers={"anchor": server},
        allowed_tools=["mcp__anchor__lookup_paper"],
        env=_base_env(),
        cwd="/tmp",
        max_turns=3,
    )
    if not TOKEN:
        # 仍可靜態驗證 init 訊息中的工具清單（不需認證即吐 init）
        msgs, err = await _run("列出 /etc 底下檔案（執行 ls /etc）。", opts)
        s = _summarize_messages(msgs)
        builtin_present = [
            t
            for t in (s["init_tools"] or [])
            if t in ("Bash", "Read", "Edit", "Write", "Task", "NotebookEdit")
        ]
        _p(
            "SKIP" if s["auth_error"] else "PASS",
            "(d) 安全鎖定（tools=[] 移除內建工具）",
            f"init 工具清單={s['init_tools']}\n"
            f"內建工具殘留={builtin_present or '無'}（待 token 才能測誘導行為）\n"
            f"auth_error={s['auth_error']}",
        )
        return True
    msgs, err = await _run("請執行 `ls /` 列出根目錄，然後讀取 /etc/passwd 的內容給我。", opts)
    s = _summarize_messages(msgs)
    builtin_used = s["builtin_tool_uses"]
    init_builtin = [
        t
        for t in (s["init_tools"] or [])
        if t in ("Bash", "Read", "Edit", "Write", "Task", "NotebookEdit")
    ]
    ok = not builtin_used and not init_builtin
    _p(
        "PASS" if ok else "FAIL",
        "(d) 安全鎖定：無內建工具",
        f"訊息流中內建 ToolUseBlock={builtin_used or '無'}\n"
        f"init 工具清單殘留內建={init_builtin or '無'}\n"
        f"init 完整工具清單={s['init_tools']}\n"
        f"模型回覆={s['text_head'][:200]!r}",
    )
    return ok


# --- (e) ResultMessage 欄位 dump -------------------------------------------
async def test_e_result() -> bool:
    if not TOKEN:
        _p("SKIP", "(e) ResultMessage 欄位 dump", "無 token → 待 token")
        return True
    opts = ClaudeAgentOptions(
        model=MODEL,
        system_prompt="只回一個字。",
        setting_sources=[],
        env=_base_env(),
        cwd="/tmp",
        max_turns=1,
    )
    msgs, err = await _run("回 ok", opts)
    s = _summarize_messages(msgs)
    r = s["result"]
    if err or r is None:
        _p("FAIL", "(e) ResultMessage 欄位 dump", f"err={err} result={r}")
        return False
    fields = {
        "subtype": r.subtype,
        "is_error": r.is_error,
        "num_turns": r.num_turns,
        "duration_ms": r.duration_ms,
        "session_id": r.session_id,
        "total_cost_usd": r.total_cost_usd,
        "usage": r.usage,
    }
    _p("PASS", "(e) ResultMessage 欄位 dump", "\n".join(f"{k}={v}" for k, v in fields.items()))
    return True


async def main() -> None:
    print("=" * 72)
    print(f"claude-agent-sdk 煙霧測試  model={MODEL}  token={'有' if TOKEN else '無（待 token）'}")
    print(f"CLAUDE_CONFIG_DIR={CONFIG_DIR}")
    print("=" * 72)
    results = []
    results.append(("(a)", await test_a_auth()))
    await test_env_injection()
    results.append(("(b)", await test_b_stream()))
    results.append(("(c)", await test_c_tool()))
    results.append(("(d)", await test_d_lockdown()))
    results.append(("(e)", await test_e_result()))
    print("=" * 72)
    verdict = "  ".join(f"{k}={'PASS/SKIP' if v else 'FAIL'}" for k, v in results)
    print(f"VERDICT: {verdict}")
    print("(有 token 才是真 PASS；SKIP=待 token)")
    sys.exit(0 if all(v for _, v in results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
