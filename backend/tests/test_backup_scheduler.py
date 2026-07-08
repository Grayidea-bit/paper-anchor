"""定時備份排程測試（M12 D10 / T-BK-04）。

不真睡：`_tick()` 抽出來獨立測（不含 `asyncio.sleep`）；`scheduler_loop()` 本身的
兩個測試把 `asyncio.sleep` monkeypatch 成假版本（丟哨兵例外跳出無窮迴圈），驗證
「tick 例外不死迴圈」與「CancelledError 會往外傳」。

settings_store 與 test_backup.py 同手法：monkeypatch `_cache` / `update()` 為
純記憶體版，不建 `settings` 表。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app import settings_store
from app.services import backup, backup_scheduler


@pytest.fixture(autouse=True)
def _reset_backup_module_state():
    """每測試重置 services/backup.py 的模組級狀態（與 test_backup.py 同作法）。"""
    backup._progress = None
    backup._last_run = None
    yield
    backup._progress = None
    backup._last_run = None


@pytest.fixture
def fake_settings_store(monkeypatch):
    """`settings_store.update()` 改為只操作記憶體 `_cache`（同 test_backup.py）。"""
    monkeypatch.setattr(settings_store, "_cache", {})

    async def _update(values: dict) -> dict:
        for key, value in values.items():
            if key not in settings_store.ALLOWED_KEYS:
                continue
            if value in ("", None):
                settings_store._cache.pop(key, None)
            else:
                settings_store._cache[key] = value
        return dict(settings_store._cache)

    monkeypatch.setattr(settings_store, "update", _update)
    return settings_store._cache


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ---------- _due：到期判斷（節流／重試規則） ----------


class TestDue:
    def test_never_run_is_due(self):
        assert backup_scheduler._due(6, datetime.now(UTC)) is True

    def test_recent_success_not_due(self):
        now = datetime.now(UTC)
        backup._last_run = {"at": _iso(now - timedelta(hours=1)), "ok": True, "counts": {}}
        assert backup_scheduler._due(6, now) is False

    def test_old_success_is_due(self):
        now = datetime.now(UTC)
        backup._last_run = {"at": _iso(now - timedelta(hours=7)), "ok": True, "counts": {}}
        assert backup_scheduler._due(6, now) is True

    def test_recent_failure_not_due(self):
        """上次失敗且剛發生：以失敗時間起算未滿 interval，不算到期（不狂重試）。"""
        now = datetime.now(UTC)
        backup._last_run = {"at": _iso(now - timedelta(minutes=1)), "ok": False, "error": "x"}
        assert backup_scheduler._due(6, now) is False

    def test_old_failure_is_due(self):
        """上次失敗且已過一個 interval：允許再次自動觸發。"""
        now = datetime.now(UTC)
        backup._last_run = {"at": _iso(now - timedelta(hours=7)), "ok": False, "error": "x"}
        assert backup_scheduler._due(6, now) is True

    def test_falls_back_to_persisted_value_on_cold_start(self, fake_settings_store):
        now = datetime.now(UTC)
        fake_settings_store["backup_last_run"] = {
            "at": _iso(now - timedelta(hours=1)),
            "ok": True,
            "counts": {},
        }
        assert backup._last_run is None
        assert backup_scheduler._due(6, now) is False


# ---------- _tick：整合上述條件 + interval/連接/併發防護 ----------


class TestTick:
    async def test_interval_zero_skips(self, fake_settings_store, monkeypatch):
        fake_settings_store["gdrive_refresh_token"] = "rtoken"
        called = {"n": 0}

        async def _fake_run_backup():
            called["n"] += 1

        monkeypatch.setattr(backup, "run_backup", _fake_run_backup)
        await backup_scheduler._tick()
        assert called["n"] == 0

    async def test_not_connected_skips(self, fake_settings_store, monkeypatch):
        fake_settings_store["backup_interval_hours"] = 6
        called = {"n": 0}

        async def _fake_run_backup():
            called["n"] += 1

        monkeypatch.setattr(backup, "run_backup", _fake_run_backup)
        await backup_scheduler._tick()
        assert called["n"] == 0

    async def test_already_running_skips(self, fake_settings_store, monkeypatch):
        fake_settings_store["gdrive_refresh_token"] = "rtoken"
        fake_settings_store["backup_interval_hours"] = 6
        called = {"n": 0}

        async def _fake_run_backup():
            called["n"] += 1

        monkeypatch.setattr(backup, "run_backup", _fake_run_backup)
        monkeypatch.setattr(backup, "is_running", lambda: True)
        await backup_scheduler._tick()
        assert called["n"] == 0

    async def test_not_due_skips(self, fake_settings_store, monkeypatch):
        fake_settings_store["gdrive_refresh_token"] = "rtoken"
        fake_settings_store["backup_interval_hours"] = 6
        backup._last_run = {"at": datetime.now(UTC).isoformat(), "ok": True, "counts": {}}
        called = {"n": 0}

        async def _fake_run_backup():
            called["n"] += 1

        monkeypatch.setattr(backup, "run_backup", _fake_run_backup)
        await backup_scheduler._tick()
        assert called["n"] == 0

    async def test_due_triggers_run_backup(self, fake_settings_store, monkeypatch):
        fake_settings_store["gdrive_refresh_token"] = "rtoken"
        fake_settings_store["backup_interval_hours"] = 6
        backup._last_run = {
            "at": (datetime.now(UTC) - timedelta(hours=7)).isoformat(),
            "ok": True,
            "counts": {},
        }
        called = {"n": 0}

        async def _fake_run_backup():
            called["n"] += 1

        monkeypatch.setattr(backup, "run_backup", _fake_run_backup)
        await backup_scheduler._tick()
        assert called["n"] == 1

    async def test_never_run_and_connected_triggers(self, fake_settings_store, monkeypatch):
        fake_settings_store["gdrive_refresh_token"] = "rtoken"
        fake_settings_store["backup_interval_hours"] = 6
        called = {"n": 0}

        async def _fake_run_backup():
            called["n"] += 1

        monkeypatch.setattr(backup, "run_backup", _fake_run_backup)
        await backup_scheduler._tick()
        assert called["n"] == 1

    async def test_failed_recently_does_not_retry_every_tick(
        self, fake_settings_store, monkeypatch
    ):
        fake_settings_store["gdrive_refresh_token"] = "rtoken"
        fake_settings_store["backup_interval_hours"] = 6
        backup._last_run = {
            "at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
            "ok": False,
            "error": "boom",
        }
        called = {"n": 0}

        async def _fake_run_backup():
            called["n"] += 1

        monkeypatch.setattr(backup, "run_backup", _fake_run_backup)
        await backup_scheduler._tick()
        assert called["n"] == 0

    async def test_failed_long_ago_retries(self, fake_settings_store, monkeypatch):
        """失敗已超過一個 interval：排程應再次觸發（手動觸發之外的自動重試路徑）。"""
        fake_settings_store["gdrive_refresh_token"] = "rtoken"
        fake_settings_store["backup_interval_hours"] = 6
        backup._last_run = {
            "at": (datetime.now(UTC) - timedelta(hours=7)).isoformat(),
            "ok": False,
            "error": "boom",
        }
        called = {"n": 0}

        async def _fake_run_backup():
            called["n"] += 1

        monkeypatch.setattr(backup, "run_backup", _fake_run_backup)
        await backup_scheduler._tick()
        assert called["n"] == 1


# ---------- scheduler_loop：迴圈本身不死、可被取消 ----------


class TestSchedulerLoop:
    async def test_loop_keeps_running_after_tick_exception(self, monkeypatch):
        """tick 內丟例外不得讓迴圈中斷：讓 tick 每次都拋錯，靠假 sleep 計數跳出。"""
        call_count = {"n": 0}

        async def _raising_tick():
            call_count["n"] += 1
            raise RuntimeError("boom")

        class _StopLoop(Exception):
            pass

        sleep_calls = {"n": 0}

        async def _fake_sleep(seconds):
            assert seconds == backup_scheduler.TICK_SECONDS
            sleep_calls["n"] += 1
            if sleep_calls["n"] >= 3:
                raise _StopLoop

        monkeypatch.setattr(backup_scheduler, "_tick", _raising_tick)
        monkeypatch.setattr(backup_scheduler.asyncio, "sleep", _fake_sleep)

        with pytest.raises(_StopLoop):
            await backup_scheduler.scheduler_loop()

        assert call_count["n"] == 3  # 三次 tick 都執行了，例外沒有讓迴圈中斷

    async def test_loop_propagates_cancelled_error(self, monkeypatch):
        """shutdown 時的 CancelledError（在 sleep 期間發生）應往外拋出，讓迴圈結束。"""

        async def _noop_tick():
            return None

        async def _cancel_sleep(seconds):
            raise asyncio.CancelledError

        monkeypatch.setattr(backup_scheduler, "_tick", _noop_tick)
        monkeypatch.setattr(backup_scheduler.asyncio, "sleep", _cancel_sleep)

        with pytest.raises(asyncio.CancelledError):
            await backup_scheduler.scheduler_loop()
