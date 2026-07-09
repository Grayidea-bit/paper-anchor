"""給 LLM 呼叫的工具（tool calling）——複製 template_tool.py 即可新增。

規則（docs/02-architecture.md D7）：
- 本套件內每個模組定義 `ENABLED: bool` 與 `TOOLS: list[callable]`。
- ENABLED=False 的模組不註冊（template_tool.py 即是）。
- 工具函式的 JSON Schema 由型別註記自動生成、描述取自 docstring（Pydantic AI）。
- 全部工具停用時 build_toolset() 回 None → 對話管線不帶 tools，行為與無此功能時完全相同。
"""

import importlib
import inspect
import logging
import pkgutil
import re
import types
import typing
from dataclasses import dataclass

logger = logging.getLogger(__name__)


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


_ARGS_SECTION_HEADERS = {
    "Args",
    "Arguments",
    "Returns",
    "Raises",
    "Yields",
    "Note",
    "Notes",
    "Example",
    "Examples",
    "Attributes",
}


def _parse_docstring(fn) -> tuple[str, dict[str, str]]:
    """解析 Google-style docstring，拆成 (摘要描述, {參數名: 描述})。

    對齊 Pydantic AI（griffe）側的行為：摘要只取 Args 等區段之前的文字，
    讓 Claude 後端與 OpenAI 後端看到的工具層級描述來源一致（見 T-FD-07）。
    """
    doc = fn.__doc__
    if not doc:
        return fn.__name__, {}

    summary_paragraphs: list[list[str]] = [[]]
    arg_docs: dict[str, str] = {}
    section = "summary"
    current_param: str | None = None
    args_indent: int | None = None

    for raw_line in doc.splitlines():
        stripped = raw_line.strip()
        header = stripped[:-1] if stripped.endswith(":") else None
        if header in _ARGS_SECTION_HEADERS:
            section = "args" if header in ("Args", "Arguments") else "other"
            current_param = None
            args_indent = None
            continue

        if section == "summary":
            if not stripped:
                if summary_paragraphs[-1]:
                    summary_paragraphs.append([])
            else:
                summary_paragraphs[-1].append(stripped)
        elif section == "args":
            if not stripped:
                continue
            indent = len(raw_line) - len(raw_line.lstrip())
            if args_indent is None:
                args_indent = indent
            m = re.match(r"^(\w+)\s*:\s*(.*)$", stripped)
            if m and indent <= args_indent:
                current_param = m.group(1)
                arg_docs[current_param] = m.group(2).strip()
            elif current_param is not None:
                arg_docs[current_param] = f"{arg_docs[current_param]} {stripped}".strip()
        # section == "other"：Returns/Raises 等不進工具描述，比照 griffe 行為忽略。

    summary = "\n\n".join(" ".join(p) for p in summary_paragraphs if p).strip()
    return summary or fn.__name__, arg_docs


def _tool_description(fn) -> str:
    """取 docstring 摘要段當工具描述（模型判斷何時呼叫的依據）。

    只取 Args 等區段之前的說明文字，與 Pydantic AI 側（OpenAI 後端）
    的工具描述來源一致（見 _parse_docstring / T-FD-07）。
    """
    summary, _ = _parse_docstring(fn)
    return summary


_PRIMITIVE_JSON_TYPES: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


def _map_annotation(anno: object, *, fn_name: str, param_name: str) -> tuple[dict, bool]:
    """把型別註記映成 JSON Schema 片段，回傳 (schema, is_optional)。

    - `Optional[X]` / `X | None`：剝殼取內型，並標記為 optional（即使沒有預設值）。
    - `list[X]`：映成 array，`items` 盡力遞迴推導元素型別。
    - `dict`（含 `dict[K, V]`）：映成 object。
    - 無法辨識的型別退回 string，並記一筆 warning（不再靜默）。
    """
    origin = typing.get_origin(anno)
    union_type = getattr(types, "UnionType", None)  # PEP 604（X | None），3.10+

    if origin is typing.Union or (union_type is not None and origin is union_type):
        union_args = typing.get_args(anno)
        is_optional = type(None) in union_args
        non_none = [a for a in union_args if a is not type(None)]
        if len(non_none) == 1:
            inner_schema, _ = _map_annotation(non_none[0], fn_name=fn_name, param_name=param_name)
            return inner_schema, is_optional
        logger.warning(
            "工具 %s 參數 %s 的聯合型別 %r 無法唯一映射為單一 JSON Schema 型別，退回 string",
            fn_name,
            param_name,
            anno,
        )
        return {"type": "string"}, is_optional

    if anno in _PRIMITIVE_JSON_TYPES:
        return {"type": _PRIMITIVE_JSON_TYPES[anno]}, False

    if anno is list or origin is list:
        item_args = typing.get_args(anno)
        if item_args:
            item_schema, _ = _map_annotation(item_args[0], fn_name=fn_name, param_name=param_name)
        else:
            item_schema = {"type": "string"}
        return {"type": "array", "items": item_schema}, False

    if anno is dict or origin is dict:
        return {"type": "object"}, False

    logger.warning(
        "工具 %s 參數 %s 的型別註記 %r 無法映射任何已知 JSON Schema 型別，退回 string",
        fn_name,
        param_name,
        anno,
    )
    return {"type": "string"}, False


def _input_schema(fn) -> dict:
    """從函式簽名 + docstring 生完整 JSON Schema，供 SDK @tool 使用。

    `claude_agent_sdk.tool()` 的 `input_schema` 若已是含 `type`/`properties`
    的 dict 會直接原樣採用（見 claude_agent_sdk 內 `_build_schema`），因此這裡
    直接組出完整 JSON Schema，而非退化成「name -> type」的簡化形式——
    讓 Claude 後端能看到跟 Pydantic AI 側（OpenAI 後端）同等品質的參數描述、
    required/optional 區分與型別（見 T-FD-07）。

    剝掉第一個 ctx 參數（RunContext，若工具收情境）；其餘規則：
    - 描述取自 docstring 的 Args 段（Google style，見 `_parse_docstring`）。
    - 有預設值的參數不進 `required`，並在描述附註預設值。
    - `Optional[X]` / `X | None` 一律視為 optional（即使沒有預設值）。
    - 型別映射見 `_map_annotation`；缺型別註記或無法辨識時退回 string 並記 warning。
    """
    sig = inspect.signature(fn)
    params = list(sig.parameters.values())
    # 第一參數是 ctx: RunContext[ToolDeps]（若工具收情境）→ 剝除
    if params and params[0].name in ("ctx", "context"):
        params = params[1:]

    _, arg_docs = _parse_docstring(fn)
    properties: dict[str, dict] = {}
    required: list[str] = []

    for p in params:
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue

        has_default = p.default is not inspect.Parameter.empty
        if p.annotation is inspect.Parameter.empty:
            logger.warning("工具 %s 參數 %s 缺型別註記，退回 string", fn.__name__, p.name)
            type_schema, is_optional = {"type": "string"}, False
        else:
            type_schema, is_optional = _map_annotation(
                p.annotation, fn_name=fn.__name__, param_name=p.name
            )

        prop = dict(type_schema)
        desc = arg_docs.get(p.name, "")
        if has_default:
            default_note = f"（預設：{p.default!r}）"
            desc = f"{desc}{default_note}" if desc else default_note
        if desc:
            prop["description"] = desc
        properties[p.name] = prop

        if not has_default and not is_optional:
            required.append(p.name)

    return {"type": "object", "properties": properties, "required": required}


def _needs_ctx(fn) -> bool:
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
