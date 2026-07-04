"""RAG 對話核心：檢索、prompt 組裝、[C{index}] 引用協定（docs/02 D1/D3）。"""

import re
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import repo

TOP_K = 8
HISTORY_LIMIT = 10
_CITATION_RE = re.compile(r"\[[Cc](\d+)\]")
_PROMPTS = Path(__file__).parent.parent / "prompts"


def load_prompt(name: str) -> str:
    return (_PROMPTS / name).read_text(encoding="utf-8")


def _language() -> str:
    return {"zh-TW": "繁體中文", "zh-CN": "簡體中文", "en": "English"}.get(
        get_settings().answer_language, get_settings().answer_language
    )


async def retrieve_context(
    session: AsyncSession,
    doc_id: int,
    query_embedding: list[float],
    selection_chunk_id: int | None = None,
) -> list[dict]:
    """向量檢索 top-k；有選取時強制加入該 chunk 與前後相鄰（D3）。"""
    chunks = await repo.similar_chunks(session, doc_id, query_embedding, TOP_K)
    if selection_chunk_id is not None:
        sel = next((c for c in chunks if c["id"] == selection_chunk_id), None)
        sel_index = sel["chunk_index"] if sel else None
        if sel_index is None:
            by_id = await repo.chunks_by_ids(session, doc_id, [selection_chunk_id])
            if by_id:
                sel_index = by_id[0]["chunk_index"]
                chunks = by_id + chunks
        if sel_index is not None:
            neighbors = await repo.chunks_by_indexes(
                session, doc_id, [sel_index - 1, sel_index + 1]
            )
            chunks = chunks + neighbors
    seen: set[int] = set()
    unique = []
    for c in chunks:
        if c["id"] not in seen:
            seen.add(c["id"])
            unique.append(c)
    return sorted(unique, key=lambda c: c["chunk_index"])


def build_messages(
    doc: dict,
    context_chunks: list[dict],
    history: list[dict],
    question: str,
    selection_text: str | None = None,
) -> list[dict]:
    system = load_prompt("chat_system.md").replace("{language}", _language())
    context_lines = [f"# 文獻：{doc['title']}", "", "# 可引用段落"]
    for c in context_chunks:
        context_lines.append(f"[C{c['chunk_index']}] (p.{c['page']}) {c['content']}")
    context_block = "\n".join(context_lines)

    messages: list[dict] = [{"role": "system", "content": system + "\n\n" + context_block}]
    for m in history[-HISTORY_LIMIT:]:
        messages.append({"role": m["role"], "content": m["content"]})
    user_content = question
    if selection_text:
        user_content = f"我選取了這段原文：\n> {selection_text}\n\n{question}"
    messages.append({"role": "user", "content": user_content})
    return messages


def parse_citations(answer: str, context_chunks: list[dict]) -> list[dict]:
    """把回答中的 [C12] 解析為結構化引用；未知編號忽略（prompt 違規容錯）。"""
    by_index = {c["chunk_index"]: c for c in context_chunks}
    citations = []
    seen: set[int] = set()
    for m in _CITATION_RE.finditer(answer):
        idx = int(m.group(1))
        if idx in seen or idx not in by_index:
            continue
        seen.add(idx)
        c = by_index[idx]
        citations.append(
            {
                "chunk_index": idx,
                "chunk_id": c["id"],
                "page": c["page"],
                "bbox_list": c["bbox_list"],
            }
        )
    return citations
