"""煙霧測試：NIM + deepseek-v4-flash + Pydantic AI tool calling（一次性，不入版控決策前）。

    python -m scripts.smoke_pydantic_ai
"""

import asyncio
import os
import sys

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

MODEL = os.environ.get("LLM_CHAT_MODEL", "deepseek-ai/deepseek-v4-flash")
BASE = os.environ.get("LLM_BASE_URL", "https://integrate.api.nvidia.com/v1")
KEY = os.environ["LLM_API_KEY"]

tool_calls_log: list[str] = []


def make_agent(with_tools: bool) -> Agent:
    model = OpenAIChatModel(MODEL, provider=OpenAIProvider(base_url=BASE, api_key=KEY))
    agent = Agent(
        model,
        instructions=(
            "你是文獻助手。回答關於論文數據的問題前，必須先用 lookup_page 工具查詢，"
            "不得憑記憶回答。查到後引用工具給的頁碼。"
            if with_tools
            else "你是文獻助手，簡短回答。"
        ),
    )
    if with_tools:

        @agent.tool_plain
        def lookup_page(keyword: str) -> str:
            """在論文中查詢關鍵字，回傳相關段落與頁碼。"""
            tool_calls_log.append(keyword)
            return (
                f"[C1421]（p.7）KSDD2 資料集上的結果："
                f"image AUROC 94.24 / pixel PRO 70.67（關鍵字：{keyword}）"
            )

    return agent


async def run_once(label: str, with_tools: bool) -> dict:
    counts: dict[str, int] = {}
    part_types: dict[str, int] = {}
    text_parts: list[str] = []
    err = None
    usage = None
    for attempt in range(4):  # NIM 免費端點容量限流重試
        counts.clear()
        part_types.clear()
        text_parts.clear()
        err = None
        agent = make_agent(with_tools)
        try:
            async with agent.run_stream_events(
                "KSDD2 的 image AUROC 是多少？" + ("請先查詢工具。" if with_tools else "")
            ) as stream:
                async for event in stream:
                    name = type(event).__name__
                    counts[name] = counts.get(name, 0) + 1
                    part = getattr(event, "part", None)
                    if part is not None and name == "PartStartEvent":
                        ptype = type(part).__name__
                        part_types[ptype] = part_types.get(ptype, 0) + 1
                    delta = getattr(event, "delta", None)
                    if delta is not None and type(delta).__name__ == "TextPartDelta":
                        text_parts.append(delta.content_delta)
                    if name == "AgentRunResultEvent":
                        try:
                            usage = event.result.usage
                        except Exception:
                            pass
            break
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {str(e)[:200]}"
            if "ResourceExhausted" in str(e) and attempt < 3:
                await asyncio.sleep(25)
                continue
            break
    return {
        "label": label,
        "counts": counts,
        "part_types": part_types,
        "answer_head": "".join(text_parts)[:200],
        "usage": str(usage),
        "error": err,
    }


async def main() -> None:
    results = []
    results.append(await run_once("no-tools baseline", with_tools=False))
    for i in range(3):
        tool_calls_log.clear()
        r = await run_once(f"tools run {i + 1}", with_tools=True)
        r["tool_invoked"] = list(tool_calls_log)
        results.append(r)
    for r in results:
        print("=" * 70)
        for k, v in r.items():
            print(f"{k}: {v}")
    ok = all(not r["error"] for r in results)
    tool_hits = sum(1 for r in results if r.get("tool_invoked"))
    print("=" * 70)
    print(f"VERDICT: errors={'none' if ok else 'YES'} tool_trigger={tool_hits}/3")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
