"""上傳後處理管線：解析 → chunk（含 page/bbox）→ embedding 入庫。

引用錨點的資訊鏈起點（CLAUDE.md 鐵律 1）：每個 chunk 必須帶
page（1-based）與 bbox_list（[[x0,y0,x1,y1], ...]，PyMuPDF 頂左原點、point 單位）。
"""

import asyncio
import logging

import fitz  # PyMuPDF

from app.db import repo
from app.db.session import SessionLocal
from app.llm import embed_passages
from app.services.digest import generate_digest

logger = logging.getLogger(__name__)

CHUNK_TARGET_CHARS = 1800
CHUNK_MAX_CHARS = 2400


def parse_pdf(file_path: str) -> tuple[str, int, list[dict]]:
    """回傳 (title, page_count, chunks)。chunk 不跨頁（docs/02 D2）。"""
    doc = fitz.open(file_path)
    try:
        if doc.needs_pass:
            raise ValueError("PDF 有密碼保護，不支援")
        title = _extract_title(doc)
        chunks: list[dict] = []
        for page_no, page in enumerate(doc, start=1):
            blocks = [
                (b[:4], b[4].strip()) for b in page.get_text("blocks") if b[6] == 0 and b[4].strip()
            ]
            chunks.extend(_pack_blocks(blocks, page_no, start_index=len(chunks)))
        if not chunks:
            raise ValueError("PDF 無可抽取文字（可能是掃描版，MVP 不支援 OCR）")
        return title, doc.page_count, chunks
    finally:
        doc.close()


def _pack_blocks(blocks: list[tuple[tuple, str]], page_no: int, start_index: int) -> list[dict]:
    """把同頁的 text block 聚合成 chunk：目標 ~1800 字元、上限 2400。"""
    chunks: list[dict] = []
    cur_texts: list[str] = []
    cur_bboxes: list[list[float]] = []
    cur_len = 0

    def flush() -> None:
        nonlocal cur_texts, cur_bboxes, cur_len
        if cur_texts:
            chunks.append(
                {
                    "chunk_index": start_index + len(chunks),
                    "page": page_no,
                    "section": None,
                    "content": "\n".join(cur_texts),
                    "bbox_list": cur_bboxes,
                }
            )
            cur_texts, cur_bboxes, cur_len = [], [], 0

    for bbox, block_text in blocks:
        # 超長單一 block：切段落硬分
        if len(block_text) > CHUNK_MAX_CHARS:
            flush()
            for i in range(0, len(block_text), CHUNK_TARGET_CHARS):
                cur_texts = [block_text[i : i + CHUNK_TARGET_CHARS]]
                cur_bboxes = [list(map(float, bbox))]
                cur_len = len(cur_texts[0])
                flush()
            continue
        if cur_len + len(block_text) > CHUNK_TARGET_CHARS and cur_texts:
            flush()
        cur_texts.append(block_text)
        cur_bboxes.append(list(map(float, bbox)))
        cur_len += len(block_text)
    flush()
    return chunks


def _extract_title(doc: fitz.Document) -> str:
    """第一頁字最大的一行當標題；fallback 用 metadata。"""
    meta_title = (doc.metadata or {}).get("title", "").strip()
    try:
        page = doc[0]
        best_size, best_text = 0.0, ""
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                dx, dy = line.get("dir", (1, 0))
                if abs(dy) > 0.1:  # 排除直排/旋轉文字（如 arXiv 側邊戳記）
                    continue
                line_text = "".join(s["text"] for s in line["spans"]).strip()
                if len(line_text) < 8 or line_text.lower().startswith("arxiv:"):
                    continue
                size = max(s["size"] for s in line["spans"])
                if size > best_size:
                    best_size, best_text = size, line_text
        return (best_text or meta_title)[:300]
    except Exception:
        return meta_title[:300]


async def ingest_document(doc_id: int, run_digest: bool = True) -> None:
    """BackgroundTask 入口。狀態機：parsing → embedding → ready | failed。

    `run_digest=False` 時跳過導讀生成（M13 還原用：dump 已帶 digest 就沿用、省最貴的
    LLM 呼叫，見 D11）。**預設 True，既有行為完全不變**（鐵律 1 相鄰，不影響引用鏈）。
    """
    async with SessionLocal() as session:
        doc = await repo.get_document(session, doc_id)
        if doc is None:
            return
        try:
            await repo.set_document_status(session, doc_id, "parsing")
            # 冪等保障（M15 T-FD-01）：任何重跑（reingest 端點、restore 修復、啟動重置後
            # 手動重試）都先清舊 chunks，避免撞 UNIQUE(document_id, chunk_index) 或留下
            # 新舊 chunk 混雜的半殘狀態。
            await repo.delete_chunks(session, doc_id)
            title, page_count, chunks = await asyncio.to_thread(parse_pdf, doc["file_path"])
            await repo.set_document_parsed(session, doc_id, title, page_count)
            chunk_ids = await repo.insert_chunks(session, doc_id, chunks)

            await repo.set_document_status(session, doc_id, "embedding")
            embeddings = await embed_passages([c["content"] for c in chunks])
            await repo.update_chunk_embeddings(session, chunk_ids, embeddings)

            # 先 ready 讓使用者可讀可問，導讀在背景補上（digest=null 時前端顯示產生中）
            await repo.set_document_status(session, doc_id, "ready")
            logger.info("ingest done: doc=%s pages=%s chunks=%s", doc_id, page_count, len(chunks))
        except Exception as e:
            logger.exception("ingest failed: doc=%s", doc_id)
            await repo.set_document_status(session, doc_id, "failed", error_msg=str(e)[:500])
            return
    if run_digest:
        await generate_digest(doc_id)
