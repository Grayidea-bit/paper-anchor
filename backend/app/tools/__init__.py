"""給 LLM 呼叫的工具（tool calling）——複製 template_tool.py 即可新增。

規則（docs/02-architecture.md D7）：
- 本套件內每個模組定義 `ENABLED: bool` 與 `TOOLS: list[callable]`。
- ENABLED=False 的模組不註冊（template_tool.py 即是）。
- 工具函式的 JSON Schema 由型別註記自動生成、描述取自 docstring（Pydantic AI）。
- 全部工具停用時 build_toolset() 回 None → 對話管線不帶 tools，行為與無此功能時完全相同。
"""

import importlib
import pkgutil
from dataclasses import dataclass


@dataclass(frozen=True)
class ToolDeps:
    """工具執行情境（對話 scope；DB 存取由工具自行開 session）。"""

    scope: str  # "document" | "project" | "library"
    doc_id: int | None
    project_id: int | None


_discovered: list | None = None


def _discover() -> list:
    global _discovered
    if _discovered is not None:
        return _discovered
    functions: list = []
    seen: set[str] = set()
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{__name__}.{info.name}")
        if not getattr(module, "ENABLED", False):
            continue
        for fn in getattr(module, "TOOLS", []):
            if fn.__name__ in seen:
                raise RuntimeError(f"重複的工具名稱：{fn.__name__}（{info.name}）")
            seen.add(fn.__name__)
            functions.append(fn)
    _discovered = functions
    return functions


def reset_cache() -> None:
    """測試用：清除 discover 快取。"""
    global _discovered
    _discovered = None


def build_toolset():
    """回傳 FunctionToolset；無啟用工具時回 None（管線零變化的安全底線）。"""
    functions = _discover()
    if not functions:
        return None
    from pydantic_ai.toolsets import FunctionToolset

    return FunctionToolset(tools=functions)


def list_tools() -> list[dict]:
    """給 GET /api/tools 的唯讀清單。"""
    return [
        {
            "name": fn.__name__,
            "description": (fn.__doc__ or "").strip().splitlines()[0] if fn.__doc__ else "",
        }
        for fn in _discover()
    ]


# ---------- Claude Agent SDK 橋接（M8）----------
# 把 _discover() 同一批 Pydantic AI 工具函式包成 Agent SDK @tool 轉接器。
# 工具業務碼零改動：轉接器用輕量 shim 帶 deps 呼叫原函式，
# ToolReturn.metadata["chunks"] 走側信道（呼叫方傳入的 sink list）。


class _CtxShim:
    """假 RunContext：本專案工具只讀 ctx.deps（見 template_tool.py）。

    比 pydantic_ai.RunContext（需 model/usage 等重量級參數）輕，
    足以覆蓋 app/tools/ 內工具的用法。
    """

    __slots__ = ("deps",)

    def __init__(self, deps: "ToolDeps") -> None:
        self.deps = deps


def _tool_description(fn) -> str:
    """取 docstring 全文當工具描述（模型判斷何時呼叫的依據）。"""
    return (fn.__doc__ or fn.__name__).strip()


def _input_schema(fn) -> dict:
    """從函式簽名生 SDK @tool 的 input_schema（簡化 dict 形式：name -> type）。

    剝掉第一個 ctx 參數（RunContext），其餘型別註記直接對應。
    未知/複雜註記退回 str（模型看得懂字串）。
    """
    import inspect

    sig = inspect.signature(fn)
    params = list(sig.parameters.values())
    # 第一參數是 ctx: RunContext[ToolDeps]（若工具收情境）→ 剝除
    if params and params[0].name in ("ctx", "context"):
        params = params[1:]
    schema: dict = {}
    for p in params:
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        anno = p.annotation
        schema[p.name] = anno if anno in (str, int, float, bool) else str
    return schema


def _needs_ctx(fn) -> bool:
    import inspect

    params = list(inspect.signature(fn).parameters.values())
    return bool(params) and params[0].name in ("ctx", "context")


def _make_adapter(fn, deps: "ToolDeps", sink: list):
    """把單一原工具函式包成 SDK @tool 轉接器（async，回 SDK content dict）。"""
    from claude_agent_sdk import tool

    async def _impl(args: dict) -> dict:
        if _needs_ctx(fn):
            result = await fn(_CtxShim(deps), **args)
        else:
            result = await fn(**args)
        # ToolReturn（有 return_value/metadata）或純字串都要容忍
        return_value = getattr(result, "return_value", result)
        metadata = getattr(result, "metadata", None)
        if isinstance(metadata, dict) and metadata.get("chunks"):
            sink.extend(metadata["chunks"])  # 側信道：引用鏈 chunks
        text = return_value if isinstance(return_value, str) else str(return_value)
        return {"content": [{"type": "text", "text": text}]}

    decorated = tool(fn.__name__, _tool_description(fn), _input_schema(fn))(_impl)
    return decorated


def build_sdk_mcp_server(deps: "ToolDeps", sink: list):
    """把啟用中的工具包成 Agent SDK MCP server（name 固定 "anchor"）。

    Args:
        deps: 對話情境，注入每次工具呼叫。
        sink: per-request list；轉接器把 ToolReturn.metadata["chunks"] append 進來，
            claude_backend 在每次 tool done 後讀取並吐 context_chunks（讀完清空）。

    無啟用工具 → 回 None（claude_backend 就不掛 mcp_servers/allowed_tools）。
    """
    functions = _discover()
    if not functions:
        return None
    from claude_agent_sdk import create_sdk_mcp_server

    adapters = [_make_adapter(fn, deps, sink) for fn in functions]
    return create_sdk_mcp_server("anchor", "0.0.1", adapters)
