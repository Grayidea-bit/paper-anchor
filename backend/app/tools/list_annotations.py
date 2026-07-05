"""內建工具：列出使用者在目前對話範圍文獻上做的標註（畫線/底色/註解）。

標註是使用者主動留下的重點與筆記，語意/關鍵字檢索都撈不到——
這個工具讓模型能回答「我畫了什麼重點」「我的筆記」之類的問題，
且結果帶 [C{id}] 標籤可被引用跳轉（比照 keyword_search）。
"""

from pydantic_ai import RunContext, ToolReturn

from app.db import repo
from app.db.session import SessionLocal
from app.tools import ToolDeps

ENABLED = True

_SNIPPET_CHARS = 200
_TYPE_LABELS = {"underline": "畫線", "highlight": "底色", "note": "註解"}
_VALID_TYPES = {"all", "underline", "highlight", "note"}


async def list_annotations(
    ctx: RunContext[ToolDeps], type_filter: str = "all", max_results: int = 50
) -> ToolReturn:
    """列出使用者在目前對話範圍文獻上做的標註（畫線/底色/註解）。使用者問「我畫的重點」「我的筆記」「我標註了什麼」時使用。

    Args:
        type_filter: 標註類型過濾，"all"（預設，全部）/"underline"（畫線）/
            "highlight"（底色）/"note"（註解）。
        max_results: 最多回傳幾筆（1-100）。
    """
    deps = ctx.deps
    limit = min(max(1, max_results), 100)
    tf = type_filter if type_filter in _VALID_TYPES else "all"
    db_type_filter = None if tf == "all" else tf

    async with SessionLocal() as session:
        annotations = await repo.list_annotations_scoped(
            session,
            document_id=deps.doc_id if deps.scope == "document" else None,
            project_id=deps.project_id if deps.scope == "project" else None,
            type_filter=db_type_filter,
            limit=limit,
        )
        if not annotations:
            return ToolReturn(return_value="目前範圍內沒有任何標註。")

        chunk_ids = []
        seen_ids: set[int] = set()
        for ann in annotations:
            cid = ann.get("chunk_id")
            if cid is not None and cid not in seen_ids:
                seen_ids.add(cid)
                chunk_ids.append(cid)

        chunks_by_doc: dict[int, dict] = {}
        for ann in annotations:
            cid = ann.get("chunk_id")
            if cid is None or cid in chunks_by_doc:
                continue
            found = await repo.chunks_by_ids(session, ann["document_id"], [cid])
            if found:
                chunks_by_doc[cid] = found[0]

    lines = []
    for ann in annotations:
        prefix = f"[C{ann['chunk_id']}]" if ann.get("chunk_id") is not None else ""
        type_label = _TYPE_LABELS.get(ann["type"], ann["type"])
        snippet = (ann.get("selected_text") or "")[:_SNIPPET_CHARS]
        line = f"{prefix}（《{ann['document_title']}》 p.{ann['page']}，{type_label}）{snippet}"
        note_text = ann.get("note_text") or ""
        if note_text:
            line += f"｜使用者註：{note_text}"
        lines.append(line)

    chunks = [chunks_by_doc[cid] for cid in chunk_ids if cid in chunks_by_doc]
    metadata = {"chunks": chunks} if chunks else None
    return ToolReturn(
        return_value=f"找到 {len(annotations)} 筆標註：\n\n" + "\n".join(lines),
        metadata=metadata,
    )


TOOLS = [list_annotations]
