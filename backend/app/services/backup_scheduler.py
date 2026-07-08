"""定時備份排程（M12 D10，T-BK-04）。

常駐 async 迴圈，每 60 秒 tick 一次；依 `settings_store` 的 `backup_interval_hours`
決定是否該觸發 `services/backup.py` 的 `run_backup()`。由 `main.py` lifespan 以
`asyncio.create_task(scheduler_loop())` 啟動，shutdown 時 `task.cancel()`。

與 `run_backup()` 既有的併發防護（`asyncio.Lock`）分工：本模組只負責「什麼時候該
打」，「打的時候會不會撞在一起」交給 `backup.is_running()` / `run_backup()` 內建
的鎖處理（`run_backup()` 已在跑時直接 no-op 返回）。直接 `await run_backup()`
而非 `create_task`：確保同一時間最多一個備份在跑，且下一次 tick 一定看得到
這次跑完後的最新 `_last_run`，不會因兩個 tick 交錯而重複判斷「到期」。

失敗後重試規則：節流基準是「上次執行紀錄（`backup_last_run`／記憶體 `_last_run`）
的 `at`，不論成功或失敗」。若只認成功（`ok=true`）的時間來算「多久沒備份」，
失敗後這個基準會停在更久以前，導致每個 tick 都判斷「已到期」而立刻重打——正是
要避免的「失敗後每分鐘狂重試」。因此不論上次結果是否成功，只要距離上次執行
未滿一個 `interval_hours` 就先跳過自動觸發；使用者仍可用 `POST /api/backup/run`
手動觸發，不受此節流限制（那條路徑不經過本模組）。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from app import settings_store
from app.services import backup

logger = logging.getLogger(__name__)

TICK_SECONDS = 60


def _parse_at(value: str) -> datetime | None:
    """解析 `backup_last_run.at` 的 ISO 字串；格式不明時回 None（視為「未知，須觸發」）。"""
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _due(interval_hours: float, now: datetime) -> bool:
    """是否已到下次自動備份時間（見模組頂註解「失敗後重試規則」）。"""
    last_run: dict[str, Any] | None = backup._last_run_or_persisted()
    if not last_run:
        return True  # 從未跑過
    at = last_run.get("at")
    if not at:
        return True
    last_at = _parse_at(at)
    if last_at is None:
        return True
    return now - last_at >= timedelta(hours=interval_hours)


async def _tick() -> None:
    """檢查一次是否該觸發定時備份；例外交給呼叫端 `scheduler_loop` 統一捕捉。"""
    interval_hours = settings_store.runtime("backup_interval_hours", 0) or 0
    if interval_hours <= 0:
        return  # 0／未設 = 關閉
    if not settings_store.runtime("gdrive_refresh_token"):
        return  # 尚未連接 Google Drive
    if backup.is_running():
        return  # 已有一次備份在跑（手動或排程觸發皆算）
    if not _due(interval_hours, datetime.now(UTC)):
        return
    await backup.run_backup()  # 直接 await，不 create_task：避免下個 tick 與本次重疊


async def scheduler_loop() -> None:
    """常駐排程迴圈，由 `main.py` lifespan 啟動；shutdown 時由外部 `task.cancel()`。

    每次 tick 內的任何例外都在此捕捉並記 log，迴圈本身不得因單次失敗而中斷；
    `asyncio.CancelledError`（shutdown 觸發）例外，直接往外拋出讓迴圈結束。
    """
    while True:
        try:
            await _tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("backup scheduler: tick 發生未預期例外")
        await asyncio.sleep(TICK_SECONDS)
