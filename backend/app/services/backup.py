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

import asyncio
import json
import logging
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from anyio import Path as AsyncPath

from app import settings_store
from app.config import get_settings
from app.db import repo
from app.db.session import SessionLocal
from app.errors import AppError
from app.services import gdrive
from app.version import APP_VERSION

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


# =====================================================================
# 編排（T-BK-03）：把上面的匯出層與 services/gdrive.py 串成一次完整備份。
# 被 routers/backup.py 的 BackgroundTask 呼叫；狀態全放模組級變數（單機單使用者，
# 同 services/gdrive.py 的 OAuth pending state 先例）。
# =====================================================================

BACKUP_FOLDER_NAME = "PaperAnchor Backup"

# non-blocking 佔用：run_backup() 開頭見已鎖住就直接 return，不排隊等待。
# asyncio.Lock.acquire() 在未鎖定時同步回傳（不經過事件迴圈讓出點），
# 故「檢查未鎖 → async with 取得」之間不會被其他 task 插隊。
# 一把服務層鎖，backup 與 restore（M13）共用——天然互斥（進行中觸發另一操作直接略過），
# 常駐排程零改動即被同一把鎖擋下（見 D11）。`_operation` 標示當前進行的操作供 status 顯示。
_lock = asyncio.Lock()
_progress: dict[str, Any] | None = None
_operation: str | None = None
_last_run: dict[str, Any] | None = None
_last_restore: dict[str, Any] | None = None


def is_running() -> bool:
    """是否有備份或還原正在進行（router 層用來回 409）。"""
    return _lock.locked()


@asynccontextmanager
async def try_begin(operation: str) -> AsyncIterator[bool]:
    """非阻塞取鎖：取得→`yield True`（呼叫者執行）；已被佔用→`yield False`（呼叫者略過）。

    離開時釋放鎖並清除 `_operation`/`_progress`。backup 與 restore 共用此 helper 與同一把
    鎖，天然互斥（見 D11）。取鎖同步完成（`asyncio.Lock` 未鎖定時 acquire 不讓出事件迴圈），
    故「檢查未鎖 → 取得」之間不會被其他 task 插隊。
    """
    global _operation, _progress
    if _lock.locked():
        yield False
        return
    async with _lock:
        _operation = operation
        try:
            yield True
        finally:
            _operation = None
            _progress = None


def set_progress(phase: str, current: int, total: int) -> None:
    """設定當前操作進度（backup 與 restore 共用；階段名見 D10/D11）。"""
    global _progress
    _progress = {"phase": phase, "current": current, "total": total}


def _last_run_or_persisted() -> dict[str, Any] | None:
    """目前已知最新一次備份紀錄（不論成功或失敗）。

    記憶體 `_last_run`（本次啟動內跑過的結果）優先；冷啟動時回落 `settings_store`
    持久化的 `backup_last_run`。供 `get_status()` 與排程模組
    （`services/backup_scheduler.py`，T-BK-04）共用。
    """
    return _last_run if _last_run is not None else settings_store.runtime("backup_last_run")


def _last_restore_or_persisted() -> dict[str, Any] | None:
    """目前已知最新一次還原紀錄；記憶體優先、冷啟動回落 `restore_last_run`（M13 D11）。"""
    return (
        _last_restore if _last_restore is not None else settings_store.runtime("restore_last_run")
    )


async def get_status() -> dict[str, Any]:
    """組出 `GET /api/backup/status` 回應（見 D10/D11 / 02-architecture.md §5）。

    `last_run`/`last_restore` 優先讀本次啟動後的記憶體結果；冷啟動（尚未跑過）時各自回落
    `settings_store` 的 `backup_last_run`/`restore_last_run` 持久化值。`operation` 標示當前
    進行中的操作（`"backup"`/`"restore"`/`None`）。
    """
    connected = bool(settings_store.runtime("gdrive_refresh_token"))
    interval_hours = settings_store.runtime("backup_interval_hours", 0) or 0
    return {
        "connected": connected,
        "running": is_running(),
        "operation": _operation,
        "progress": _progress,
        "last_run": _last_run_or_persisted(),
        "last_restore": _last_restore_or_persisted(),
        "interval_hours": interval_hours,
    }


def _safe_error_message(exc: Exception) -> str:
    """轉成可持久化／可顯示的錯誤訊息；`AppError`（含 GDrive 例外）訊息已不含秘密可直接用。

    未預期例外一律不外洩內部細節，只記 log。
    """
    if isinstance(exc, AppError):
        return exc.message
    logger.exception("backup: run_backup 發生未預期例外")
    return "備份失敗，請查看伺服器日誌"


async def run_backup() -> None:
    """備份主編排；由 BackgroundTask 呼叫。

    已在跑時直接跳過（見 `is_running`／模組頂註解）。任一步驟失敗即中止，manifest
    不會上傳，遠端維持上次完整備份基準（D10「上傳順序」）；`_last_run` 記錄失敗
    原因並持久化，staging 一律清理。
    """
    global _last_run
    async with try_begin("backup") as acquired:
        if not acquired:
            return
        staging: Path | None = None
        try:
            root_id = await gdrive.ensure_folder(BACKUP_FOLDER_NAME)
            db_folder_id = await gdrive.ensure_folder("db", parent_id=root_id)
            pdfs_folder_id = await gdrive.ensure_folder("pdfs", parent_id=root_id)

            async with SessionLocal() as session:
                documents = await repo.dump_table_rows(session, "documents")

            remote_pdfs = await gdrive.list_folder(pdfs_folder_id)
            remote_pdf_names = {f["name"] for f in remote_pdfs}

            pending_pdfs: list[tuple[str, str]] = []
            for doc in documents:
                file_path = doc.get("file_path")
                if not file_path:
                    continue
                path = AsyncPath(file_path)
                if not await path.exists():
                    continue
                name = Path(file_path).name
                if name in remote_pdf_names:
                    continue
                pending_pdfs.append((name, file_path))

            total_pdfs = len(pending_pdfs)
            set_progress("pdfs", 0, total_pdfs)
            for i, (name, file_path) in enumerate(pending_pdfs, start=1):
                await gdrive.upload_file(pdfs_folder_id, name, file_path, "application/pdf")
                set_progress("pdfs", i, total_pdfs)

            staging = prepare_staging()
            counts = await export_db_dumps(staging)

            db_files = sorted((staging / "db").glob("*.json"))
            remote_db_files = await gdrive.list_folder(db_folder_id)
            remote_db_by_name = {f["name"]: f["id"] for f in remote_db_files}

            total_db = len(db_files)
            set_progress("db", 0, total_db)
            for i, db_file in enumerate(db_files, start=1):
                content = db_file.read_bytes()
                name = db_file.name
                if name in remote_db_by_name:
                    await gdrive.update_file(remote_db_by_name[name], content, "application/json")
                else:
                    await gdrive.upload_file(db_folder_id, name, content, "application/json")
                set_progress("db", i, total_db)

            manifest = await build_manifest(counts)
            manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")

            set_progress("manifest", 0, 1)
            root_files = await gdrive.list_folder(root_id)
            manifest_entry = next((f for f in root_files if f["name"] == "manifest.json"), None)
            if manifest_entry:
                await gdrive.update_file(manifest_entry["id"], manifest_bytes, "application/json")
            else:
                await gdrive.upload_file(
                    root_id, "manifest.json", manifest_bytes, "application/json"
                )
            set_progress("manifest", 1, 1)

            _last_run = {
                "at": datetime.now(UTC).isoformat(),
                "ok": True,
                "counts": manifest["counts"],
            }
            await settings_store.update({"backup_last_run": _last_run})
        except Exception as exc:
            error_message = _safe_error_message(exc)
            _last_run = {
                "at": datetime.now(UTC).isoformat(),
                "ok": False,
                "error": error_message,
            }
            await settings_store.update({"backup_last_run": _last_run})
        finally:
            if staging is not None:
                cleanup_staging(staging)
