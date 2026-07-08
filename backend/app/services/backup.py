"""單向備份到 Google Drive — 匯出層（M12 D10，T-BK-02）。

本模組只做「匯出」：把白名單表 dump 成 staging 目錄下的 JSON 檔，並組出 manifest.json
的內容。**不含**任何上傳、進度追蹤、併發防護、排程或 routers 邏輯——那些是 T-BK-03 /
T-BK-04 的範圍。刻意不 import `app.services.gdrive`，也不 import `app.main`（避免與
routers/main 形成循環匯入；services 層只往 db 層方向依賴）。

給 T-BK-03 的公開介面（呼叫順序建議）：

    staging = prepare_staging()                 # 或傳自訂 base_dir（測試用 tmp_path）
    try:
        counts = await export_db_dumps(staging) # 寫 staging/db/*.json，回傳六表 row 數
        manifest = await build_manifest(counts) # 組 manifest.json 內容（含 pdfs 清單）
        # ... 上傳 db/*.json → 上傳 pdfs（直接串流 /data/uploads，不落地）→ 最後上傳 manifest ...
    finally:
        cleanup_staging(staging)

manifest 上傳必須放在最後一步：任一步失敗即中止本輪且不上傳 manifest.json，讓遠端維持
上一次的完整備份基準（見 D10「上傳順序」）。
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from anyio import Path as AsyncPath

from app import settings_store
from app.config import get_settings
from app.db import repo
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

# 白名單表，需與 repo._DUMP_TABLE_COLUMNS 的 key 一致（不含 chunks / embedding）。
DUMP_TABLES: tuple[str, ...] = (
    "documents",
    "projects",
    "annotations",
    "glossary_entries",
    "conversations",
    "messages",
)

FORMAT_VERSION = 1

# 對齊 backend/app/main.py 的 `FastAPI(version=...)`。services 層不 import app.main
# （main → routers.backup → services.backup 會與 `from app.main import app` 形成循環匯入），
# 故在此手動同步；main.py 版本號變動時記得一併更新。
APP_VERSION = "0.1.0"


def _default_staging_root() -> Path:
    """預設 staging 路徑：資料目錄（upload_dir）旁的 backup_staging。"""
    upload_dir = Path(get_settings().upload_dir)
    return upload_dir.parent / "backup_staging"


def prepare_staging(base_dir: Path | None = None) -> Path:
    """建立乾淨的 staging 目錄（含 db/ 子目錄），回傳其路徑。

    若目錄已存在（例如上次備份異常中止留下殘留），先清空再重建，避免舊 dump 混入
    這次備份。`base_dir` 可注入（測試用 tmp_path）；未提供時使用預設路徑。
    """
    staging_dir = base_dir if base_dir is not None else _default_staging_root()
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    (staging_dir / "db").mkdir(parents=True, exist_ok=True)
    return staging_dir


def cleanup_staging(staging_dir: Path) -> None:
    """刪除 staging 目錄（上傳完成或失敗後皆應呼叫，dump 只暫存不常駐）。"""
    shutil.rmtree(staging_dir, ignore_errors=True)


def _write_json(path: Path, data: Any) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


async def export_db_dumps(staging_dir: Path) -> dict[str, int]:
    """匯出六張白名單表到 `staging_dir/db/{table}.json`，另寫 `db/settings.json`。

    settings.json 僅含 settings_store 快取中非 SECRET_KEYS 的鍵（鐵律 6 / D10）。
    回傳 `{table: row_count}`（六表，不含 settings——settings 不是「一張表」的列數概念）。
    """
    db_dir = staging_dir / "db"
    db_dir.mkdir(parents=True, exist_ok=True)

    counts: dict[str, int] = {}
    async with SessionLocal() as session:
        for table in DUMP_TABLES:
            rows = await repo.dump_table_rows(session, table)
            _write_json(db_dir / f"{table}.json", rows)
            counts[table] = len(rows)

    settings_cache = await settings_store.ensure_loaded()
    safe_settings = {k: v for k, v in settings_cache.items() if k not in settings_store.SECRET_KEYS}
    _write_json(db_dir / "settings.json", safe_settings)

    return counts


async def build_manifest(counts: dict[str, int]) -> dict[str, Any]:
    """組 manifest.json 內容（D10 匯出格式 v1）。

    `counts` 是 `export_db_dumps` 回傳的六表 row 數；pdfs 清單另行查 documents 表
    （不假設呼叫者已備妥），取 file_path 的 basename 與實體檔大小。檔案已遺失（本機
    刪除/搬移過)的文獻直接跳過並記 log warning，不中止備份（見 D10 刪除語意）。
    """
    settings = get_settings()

    async with SessionLocal() as session:
        documents = await repo.dump_table_rows(session, "documents")

    pdfs: list[dict[str, Any]] = []
    for doc in documents:
        file_path = doc.get("file_path")
        if not file_path:
            continue
        path = AsyncPath(file_path)
        if not await path.exists():
            logger.warning(
                "backup: pdf missing for document_id=%s file_path=%s", doc["id"], file_path
            )
            continue
        stat = await path.stat()
        pdfs.append({"name": path.name, "document_id": doc["id"], "size": stat.st_size})

    manifest_counts = {**counts, "pdfs": len(pdfs)}

    return {
        "format_version": FORMAT_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "app_version": APP_VERSION,
        "embed_model": settings.embed_model,
        "embed_dim": settings.embed_dim,
        "counts": manifest_counts,
        "pdfs": pdfs,
    }
