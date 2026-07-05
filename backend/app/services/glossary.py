"""翻譯表（glossary）服務（T-TR-01 / T-TR-04）。

兩種建立路徑：
1. 直接圈選加入（fallback，無 source_text）→ 呼叫 LLM 譯成使用者設定的目標語言。
2. 從對話「翻譯」動作萃取（T-TR-04，有 source_text）→ 帶著詳細翻譯全文，
   用 glossary_extract prompt 萃取「簡潔譯文 + 白話註解」兩行。
LLM 失敗不擋建立：條目仍存，translation/notes 留空字串，前端可用 retranslate 重試。
"""

import logging
import re

from sqlalchemy.ext.asyncio import AsyncSession

from app import settings_store
from app.db import repo
from app.llm import chat
from app.services.rag import load_prompt

logger = logging.getLogger(__name__)

DEFAULT_TARGET_LANG = "繁體中文"
CONTEXT_CHAR_BUDGET = 800

_EXTRACT_RE = re.compile(
    r"譯文[：:]\s*(?P<translation>.*?)\s*\n\s*註解[：:]\s*(?P<notes>.*)",
    re.DOTALL,
)


def _target_lang() -> str:
    return settings_store.runtime("translation_target_lang") or DEFAULT_TARGET_LANG


async def _translate(term: str, context: str, target_lang: str) -> str:
    """呼叫 LLM 翻譯術語；失敗時擲出 LLMError（由呼叫端決定降級）。"""
    prompt = (
        load_prompt("translate_term.md")
        .replace("{term}", term)
        .replace("{context}", context)
        .replace("{target_lang}", target_lang)
    )
    answer, _usage = await chat(
        [{"role": "user", "content": prompt}],
        max_tokens=300,
    )
    return answer.strip()


def _parse_extraction(raw: str) -> tuple[str, str]:
    """解析「譯文：/註解：」兩行格式；解析失敗降級：整段 strip 當 translation，notes 空字串。"""
    match = _EXTRACT_RE.search(raw)
    if not match:
        return raw.strip(), ""
    translation = match.group("translation").strip()
    notes = match.group("notes").strip()
    if not translation:
        return raw.strip(), ""
    return translation, notes


async def _extract_from_source(term: str, source_text: str, target_lang: str) -> tuple[str, str]:
    """呼叫 LLM 從詳細翻譯全文萃取（譯文, 註解）；失敗時擲出例外（由呼叫端決定降級）。"""
    prompt = (
        load_prompt("glossary_extract.md")
        .replace("{term}", term)
        .replace("{target_lang}", target_lang)
        .replace("{source_text}", source_text)
    )
    answer, _usage = await chat(
        [{"role": "user", "content": prompt}],
        max_tokens=500,
    )
    return _parse_extraction(answer)


async def create_entry(
    session: AsyncSession,
    document_id: int,
    *,
    term: str,
    page: int,
    bbox_list: list,
    chunk_id: int | None = None,
    source_text: str | None = None,
) -> dict:
    """建立翻譯表條目：讀目標語言設定 → 翻譯／萃取 → 存庫。

    有 `source_text`（T-TR-04，來自對話「翻譯」動作的詳細翻譯全文）：
    用 glossary_extract prompt 萃取「簡潔譯文 + 白話註解」。
    無 `source_text`（fallback，直接圈選加入）：走原 translate_term 路徑，notes 存空字串。
    LLM 失敗時條目仍建立，translation/notes 存空字串（不擲例外，不讓整個請求 500）。
    """
    target_lang = _target_lang()

    context = ""
    if chunk_id is not None:
        chunk = await repo.get_chunk(session, chunk_id)
        if chunk is not None:
            context = (chunk["content"] or "")[:CONTEXT_CHAR_BUDGET]

    translation = ""
    notes = ""
    try:
        if source_text:
            translation, notes = await _extract_from_source(term, source_text, target_lang)
        else:
            translation = await _translate(term, context, target_lang)
    except Exception:
        logger.exception("glossary translate failed: document=%s term=%s", document_id, term)

    return await repo.create_glossary_entry(
        session,
        document_id,
        term=term,
        translation=translation,
        target_lang=target_lang,
        page=page,
        bbox_list=bbox_list,
        chunk_id=chunk_id,
        notes=notes,
    )


async def retranslate(session: AsyncSession, entry_id: int) -> dict | None:
    """重打一次翻譯並更新該條目；條目不存在回 None。"""
    entry = await repo.get_glossary_entry(session, entry_id)
    if entry is None:
        return None
    context = ""
    if entry["chunk_id"] is not None:
        chunk = await repo.get_chunk(session, entry["chunk_id"])
        if chunk is not None:
            context = (chunk["content"] or "")[:CONTEXT_CHAR_BUDGET]

    try:
        translation = await _translate(entry["term"], context, entry["target_lang"])
    except Exception:
        logger.exception("glossary retranslate failed: entry=%s", entry_id)
        return entry

    return await repo.update_glossary_translation(session, entry_id, translation)
