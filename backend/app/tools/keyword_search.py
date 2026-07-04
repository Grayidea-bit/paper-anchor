"""內建工具：在目前對話範圍內全文檢索關鍵字（語意檢索的互補）。

向量檢索抓語意相近，但「找出所有提到 XXX 的地方」這種精確比對
它常漏——這個工具補上，且結果帶 [C{id}] 標籤可被引用跳轉。
"""

from pydantic_ai import RunContext, ToolReturn

from app.db import repo
from app.db.session import SessionLocal
from app.tools import ToolDeps

ENABLED = True

_SNIPPET_CHARS = 300


async def keyword_search(
    ctx: RunContext[ToolDeps], query: str, max_results: int = 5
) -> ToolReturn:
    """在目前對話範圍的文獻全文中精確檢索關鍵字，回傳帶 [C編號] 的段落（可直接引用）。

    需要找出「所有提到某詞的段落」或語意檢索不可靠時使用。

    Args:
        query: 要檢索的關鍵字或片語（精確比對，不是語意搜尋）。
        max_results: 最多回傳幾段（1-10）。
    """
    deps = ctx.deps
    k = min(max(1, max_results), 10)
    async with SessionLocal() as session:
        chunks = await repo.search_chunks_scoped(
            session,
            query,
            k,
            doc_id=deps.doc_id if deps.scope == "document" else None,
            project_id=deps.project_id if deps.scope == "project" else None,
        )
    if not chunks:
        return ToolReturn(return_value=f"找不到包含「{query}」的段落。")
    lines = [
        f"[C{c['id']}]（《{c['document_title']}》 p.{c['page']}）"
        f"{c['content'][:_SNIPPET_CHARS]}"
        for c in chunks
    ]
    return ToolReturn(
        return_value=f"找到 {len(chunks)} 段：\n\n" + "\n\n".join(lines),
        metadata={"chunks": chunks},
    )


TOOLS = [keyword_search]
