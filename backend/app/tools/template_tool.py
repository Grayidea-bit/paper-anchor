"""新工具模板——複製本檔、改名、把 ENABLED 改成 True，就完成新增。

工具是「給 LLM 呼叫的函式」（tool calling）。模型會根據函式的
docstring 與參數型別，自行決定何時呼叫、帶什麼參數。

三件事要知道：

1. **參數 schema 自動生成**：函式的型別註記（str/int/預設值）會被
   Pydantic AI 轉成 JSON Schema 給模型。docstring 是模型判斷「何時
   該用這個工具」的唯一線索——寫清楚用途與時機。

2. **需要對話情境就收 RunContext**：`ctx.deps` 是 ToolDeps
   （scope / doc_id / project_id），讓工具知道現在在跟哪篇文獻、
   哪個專案對話。不需要情境的純函式可省略 ctx 參數。

3. **想讓回傳內容可被引用跳轉**：回傳 `ToolReturn`，把文字放
   `return_value`（給模型看，段落用 `[C{chunk_id}]` 前綴），並把
   chunk dict（含 id/document_id/chunk_index/page/bbox_list/
   document_title/content，與檢索結果同構）放進
   `metadata={"chunks": [...]}` ——模型引用 [C{id}] 時，前端就能
   點擊跳轉高亮。純文字工具直接回傳 str 即可。

範例參考同目錄的 keyword_search.py（完整的可引用工具）。
"""

from pydantic_ai import RunContext

from app.tools import ToolDeps

# 改成 True 才會註冊到對話管線
ENABLED = False


async def my_tool(ctx: RunContext[ToolDeps], query: str, limit: int = 5) -> str:
    """一句話說明這個工具做什麼、什麼時候該用（模型會讀這行）。

    Args:
        query: 參數說明也會進 schema。
        limit: 有預設值的參數對模型是選填。
    """
    deps = ctx.deps  # deps.scope / deps.doc_id / deps.project_id
    return f"（示範）scope={deps.scope}，你查了「{query}」，上限 {limit} 筆。"


# 本模組要註冊的工具函式列表（可多個）
TOOLS = [my_tool]
