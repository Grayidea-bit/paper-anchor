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
