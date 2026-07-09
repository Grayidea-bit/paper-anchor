"""RAG 對話核心：scope 化檢索、prompt 組裝、[C{chunk_id}] 引用協定（docs/02 D1/D3/D6）。

引用標籤使用全域唯一的 chunk id（非 chunk_index）：跨文獻不撞號、
多輪對話的歷史標籤永遠指涉同一 chunk。
"""

import re
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app import settings_store
from app.config import get_settings
from app.db import repo

TOP_K_DOCUMENT = 8
TOP_K_MULTI = 12
HISTORY_LIMIT = 10
# Citation regex: supports [C123], [c123], and bare C123 formats per docs/02-architecture D1
_CITATION_RE = re.compile(r"(?:\[)?[Cc](\d+)(?:\])?")
_PROMPTS = Path(__file__).parent.parent / "prompts"
_PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")


def load_prompt(name: str, expected_placeholders: set[str] | None = None) -> str:
    """Load a prompt file and optionally verify expected placeholders exist.

    Args:
        name: Prompt filename
        expected_placeholders: Set of placeholder names to verify (e.g. {"language", "context"})

    Raises:
        ValueError: If expected placeholders are missing from the prompt
    """
    content = (_PROMPTS / name).read_text(encoding="utf-8")

    if expected_placeholders is not None:
        found = set(_PLACEHOLDER_RE.findall(content))
        missing = expected_placeholders - found
        if missing:
            raise ValueError(
                f"Missing placeholders in {name}: {sorted(missing)}. Found: {sorted(found)}"
            )

    return content


def language_name(code: str | None = None) -> str:
    """語言代碼 → prompt 用的語言名稱；digest.py 也共用。"""
    code = code or get_settings().answer_language
    return {"zh-TW": "繁體中文", "zh-CN": "簡體中文", "en": "English"}.get(code, code)


async def retrieve_context(
    session: AsyncSession,
    query_embedding: list[float],
    *,
    scope: str,
    doc_id: int | None = None,
    project_id: int | None = None,
    selection_chunk_id: int | None = None,
) -> list[dict]:
    """依 scope 檢索（隔離在 SQL 層，見 repo.similar_chunks_scoped）。

    selection 擴充（強制加入選取 chunk 與前後相鄰）僅 document scope 生效。
    """
    if scope == "document":
        chunks = await repo.similar_chunks_scoped(
            session, query_embedding, TOP_K_DOCUMENT, doc_id=doc_id
        )
        if selection_chunk_id is not None and doc_id is not None:
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
    elif scope == "project":
        chunks = await repo.similar_chunks_scoped(
            session, query_embedding, TOP_K_MULTI, project_id=project_id
        )
    else:  # library
        chunks = await repo.similar_chunks_scoped(session, query_embedding, TOP_K_MULTI)

    seen: set[int] = set()
    unique = []
    for c in chunks:
        if c["id"] not in seen:
            seen.add(c["id"])
            unique.append(c)
    # 同文獻段落相鄰、依原文順序排列
    return sorted(unique, key=lambda c: (c["document_id"], c["chunk_index"]))


def build_system(
    context_chunks: list[dict],
    *,
    scope: str = "document",
    scope_title: str | None = None,
    language: str | None = None,
) -> str:
    """系統提示詞 + 可引用段落 context block（agent 的 instructions）。"""
    system = load_prompt("chat_system.md", expected_placeholders={"language"}).replace(
        "{language}", language_name(language)
    )
    # 設定頁的附加系統提示詞（使用者自訂，附在守則之後）
    extra = settings_store.runtime("system_prompt_extra")
    if extra:
        system += f"\n\n## 使用者附加指示\n\n{extra}"

    multi_doc = scope != "document"
    if multi_doc:
        titles = sorted({c["document_title"] for c in context_chunks})
        header = f"# 專案：{scope_title}" if scope == "project" else "# 範圍：全部文獻"
        sources = "涵蓋文獻：" + "；".join(f"《{t}》" for t in titles)
        context_lines = [header, sources, "", "# 可引用段落"]
        for c in context_chunks:
            context_lines.append(
                f"[C{c['id']}]（《{c['document_title']}》 p.{c['page']}）{c['content']}"
            )
    else:
        context_lines = [f"# 文獻：{scope_title}", "", "# 可引用段落"]
        for c in context_chunks:
            context_lines.append(f"[C{c['id']}] (p.{c['page']}) {c['content']}")
    return system + "\n\n" + "\n".join(context_lines)


def build_user_content(question: str, selection_text: str | None = None) -> str:
    if selection_text:
        return f"我選取了這段原文：\n> {selection_text}\n\n{question}"
    return question


def build_messages(
    context_chunks: list[dict],
    history: list[dict],
    question: str,
    *,
    scope: str = "document",
    scope_title: str | None = None,
    selection_text: str | None = None,
    language: str | None = None,
) -> list[dict]:
    """OpenAI 格式 messages（digest/測試沿用；對話管線改走 agent.stream_chat）。"""
    system = build_system(context_chunks, scope=scope, scope_title=scope_title, language=language)
    messages: list[dict] = [{"role": "system", "content": system}]
    # 空訊息（曾中斷的串流殘留）不進 prompt
    for m in [m for m in history if m["content"].strip()][-HISTORY_LIMIT:]:
        messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": build_user_content(question, selection_text)})
    return messages


def parse_citations(answer: str, context_chunks: list[dict]) -> list[dict]:
    """把回答中的 [C{id}] 解析為結構化引用；未知編號忽略（prompt 違規容錯）。"""
    by_id = {c["id"]: c for c in context_chunks}
    citations = []
    seen: set[int] = set()
    for m in _CITATION_RE.finditer(answer):
        label = int(m.group(1))
        if label in seen or label not in by_id:
            continue
        seen.add(label)
        c = by_id[label]
        citations.append(
            {
                "label": label,
                "chunk_id": c["id"],
                "chunk_index": c["chunk_index"],
                "page": c["page"],
                "bbox_list": c["bbox_list"],
                "document_id": c["document_id"],
                "document_title": c["document_title"],
            }
        )
    return citations
