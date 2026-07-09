"""reembed 維護動作（M14 D12 / T-EM-02）：切換 `embed_source` 後全庫重建向量。

混模型向量共存會污染檢索（不同模型的向量空間不可比）。本模組逐文獻讀已存 chunk
`content` → `embed_passages` → `update_chunk_embeddings`——**content 已在 DB，免重
解析、免呼叫 chat LLM**，故比 reingest 便宜很多，適合切換 `embed_source` 後一鍵重建。

只重嵌 `status == "ready"` 的文獻：`failed`／transient 殘態文獻本就沒有可信賴的
chunk 內容鏈（或根本沒切過塊），走既有 `/reingest` 路徑（重新解析＋切塊＋嵌入）才是
正確修復手段，混進 reembed 只會徒增複雜度。

**三方共用鎖**：沿用 `services/backup.py` 的 `try_begin(operation)`／`set_progress`
helper——backup／restore／reembed 三方互斥（防混模型向量在重嵌途中被 dump）。已在
跑（任一操作）時 `try_begin` 直接 `yield False`，本函式 no-op 返回；409 判斷在
`routers/maintenance.py`（`backup.is_running()`）。進度以「篇」為單位寫入既有
`status` 的 `operation:"reembed"`（`current`/`total`），與 backup/restore 進度共用
同一組模組級變數。
"""

from __future__ import annotations

import logging

from app.db import repo
from app.db.session import SessionLocal
from app.llm import embed_passages
from app.services import backup

logger = logging.getLogger(__name__)


async def run_reembed() -> None:
    """reembed 主編排；由 BackgroundTask 呼叫。與 backup/restore 共用同一把鎖。

    逐篇處理，單篇失敗記 log 續跑（不中止整批，避免一篇壞資料卡住全庫重嵌）；
    結尾記一筆 summary log。不持久化 last_run（D12 未定義，reembed 只走進度輪詢，
    完成與否由 `GET /api/backup/status` 的 `running`/`operation` 回歸 null 判斷）。

    中斷語意（M14 審查 M2）：逐篇 commit、跨篇非原子——中途中斷（重啟或單篇失敗
    續跑）會留下部分新模型、部分舊模型的混合向量，重跑前檢索品質可能不一致；
    再觸發一次 reembed 即收斂修復（全庫 ready 文獻重嵌，冪等）。
    """
    async with backup.try_begin("reembed") as acquired:
        if not acquired:
            return

        async with SessionLocal() as session:
            documents = await repo.list_documents(session)
        ready_docs = [d for d in documents if d["status"] == "ready"]

        total = len(ready_docs)
        backup.set_progress("reembed", 0, total)
        failed_titles: list[str] = []

        for i, doc in enumerate(ready_docs, start=1):
            doc_id = doc["id"]
            try:
                async with SessionLocal() as session:
                    chunks = await repo.get_chunks(session, doc_id, limit=None)
                    if chunks:
                        embeddings = await embed_passages([c["content"] for c in chunks])
                        await repo.update_chunk_embeddings(
                            session, [c["id"] for c in chunks], embeddings
                        )
            except Exception:
                logger.exception("reembed: 文獻重嵌失敗 doc_id=%s", doc_id)
                failed_titles.append(doc.get("title") or str(doc_id))
            backup.set_progress("reembed", i, total)

        if failed_titles:
            logger.warning(
                "reembed: 完成，%d/%d 篇失敗：%s",
                len(failed_titles),
                total,
                ", ".join(failed_titles),
            )
        else:
            logger.info("reembed: 完成，共 %d 篇", total)
