"""翻譯表（glossary）服務（T-TR-01）。

使用者從 PDF 圈選術語 →「加入翻譯表」→ 呼叫 LLM 譯成使用者設定的目標語言。
LLM 失敗不擋建立：條目仍存，translation 留空字串，前端可用 retranslate 重試。
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app import settings_store
from app.db import repo
from app.llm import chat
from app.services.rag import load_prompt

logger = logging.getLogger(__name__)

DEFAULT_TARGET_LANG = "繁體中文"
CONTEXT_CHAR_BUDGET = 800


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


async def create_entry(
    session: AsyncSession,
    document_id: int,
    *,
    term: str,
    page: int,
    bbox_list: list,
    chunk_id: int | None = None,
) -> dict:
    """建立翻譯表條目：讀目標語言設定 → 取上下文 → 呼叫 LLM → 存庫。

    LLM 失敗時條目仍建立，translation 存空字串（不擲例外，不讓整個請求 500）。
    """
    target_lang = _target_lang()

    context = ""
    if chunk_id is not None:
        chunk = await repo.get_chunk(session, chunk_id)
        if chunk is not None:
            context = (chunk["content"] or "")[:CONTEXT_CHAR_BUDGET]

    translation = ""
    try:
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
