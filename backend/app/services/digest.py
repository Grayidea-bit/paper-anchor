"""自動導讀生成（F3）：文獻 ready 後在背景產生結構化導讀卡。

導讀不擋 ready：失敗只記 log，digest 維持 null，前端顯示重試入口。
長文獻（超過字元預算）取「頭 70% + 尾 30%」段落，中段捨棄並在 prompt 註明。
"""

import json
import logging

from app.db import repo
from app.db.session import SessionLocal
from app.llm import chat, extract_json
from app.services.rag import language_name, load_prompt

logger = logging.getLogger(__name__)

CONTEXT_CHAR_BUDGET = 90_000
SECTION_KEYS = ["research_question", "method", "findings", "contributions", "limitations"]


def _select_chunks(chunks: list[dict]) -> tuple[list[dict], bool]:
    total = sum(len(c["content"]) for c in chunks)
    if total <= CONTEXT_CHAR_BUDGET:
        return chunks, False
    head_budget = int(CONTEXT_CHAR_BUDGET * 0.7)
    tail_budget = CONTEXT_CHAR_BUDGET - head_budget
    head, used = [], 0
    for c in chunks:
        if used + len(c["content"]) > head_budget:
            break
        head.append(c)
        used += len(c["content"])
    tail, used = [], 0
    for c in reversed(chunks):
        if used + len(c["content"]) > tail_budget or c in head:
            break
        tail.insert(0, c)
        used += len(c["content"])
    return head + tail, True


async def generate_digest(doc_id: int, language: str | None = None) -> None:
    async with SessionLocal() as session:
        doc = await repo.get_document(session, doc_id)
        if doc is None:
            return
        chunks = await repo.get_chunks(session, doc_id)
        if not chunks:
            return
        try:
            selected, truncated = _select_chunks(chunks)
            system = load_prompt("digest_system.md").replace(
                "{language}", language_name(language)
            )
            lines = [f"文獻標題：{doc['title']}", ""]
            if truncated:
                lines.append("（注意：文獻過長，中段部分段落已省略）")
            for c in selected:
                lines.append(f"[C{c['chunk_index']}] (p.{c['page']}) {c['content']}")
            answer, usage = await chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": "\n".join(lines)},
                ],
                max_tokens=3000,
            )
            digest = _validate(extract_json(answer), chunks)
            await repo.update_document_digest(session, doc_id, digest, usage)
            logger.info("digest done: doc=%s tokens=%s", doc_id, usage)
        except Exception:
            logger.exception("digest failed: doc=%s", doc_id)


def _validate(raw: dict, chunks: list[dict]) -> dict:
    """檢查結構並把 citations 編號解析為含 page/bbox 的完整引用物件。"""
    by_index = {c["chunk_index"]: c for c in chunks}
    sections = []
    raw_sections = {s.get("key"): s for s in raw.get("sections", []) if isinstance(s, dict)}
    titles = {
        "research_question": "研究問題",
        "method": "方法",
        "findings": "主要發現",
        "contributions": "貢獻",
        "limitations": "限制",
    }
    for key in SECTION_KEYS:
        s = raw_sections.get(key, {})
        citations = []
        for idx in s.get("citations", []):
            c = by_index.get(int(idx)) if str(idx).lstrip("-").isdigit() else None
            if c:
                citations.append(
                    {
                        "chunk_index": c["chunk_index"],
                        "chunk_id": c["id"],
                        "page": c["page"],
                        "bbox_list": c["bbox_list"]
                        if isinstance(c["bbox_list"], list)
                        else json.loads(c["bbox_list"]),
                    }
                )
        sections.append(
            {
                "key": key,
                "title": s.get("title") or titles[key],
                "text": s.get("text") or "（產生失敗）",
                "citations": citations,
            }
        )
    return {"tldr": raw.get("tldr", ""), "sections": sections}
