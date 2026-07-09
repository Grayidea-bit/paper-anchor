"""備份編排 + API 端點測試（M12 D10 / T-BK-03）。

gdrive 全 mock（不打真 API，見 `_FakeGDrive`）；settings_store 以 monkeypatch 直接
操作 `_cache` 並把 `update()` 換成純記憶體版本（同 test_settings_store.py 既有作法），
不建 `settings` 表。`services/backup.py` 的 SessionLocal 換成測試 session_maker
（同 test_backup_export.py 的 `backup_db` fixture 手法），並補上 conversations/
messages 表供 `export_db_dumps` 讀六表齊全。
"""

import asyncio

import pytest
from sqlalchemy import text

from app import settings_store
from app.routers import backup as backup_router
from app.services import backup, gdrive

# ---------- 共用 fixture ----------


@pytest.fixture(autouse=True)
def _reset_backup_module_state():
    """每測試重置編排模組級狀態（lock 由 `async with` 保證釋放，這裡只清進度/結果）。"""
    backup._progress = None
    backup._last_run = None
    yield
    backup._progress = None
    backup._last_run = None


@pytest.fixture(autouse=True)
def _reset_gdrive_oauth_state():
    """避免 gdrive 模組級 OAuth 記憶體狀態跨測試（甚至跨測試檔）汙染。"""
    gdrive._pending.clear()
    gdrive._access_token = None
    gdrive._access_expires_at = 0.0
    yield
    gdrive._pending.clear()
    gdrive._access_token = None
    gdrive._access_expires_at = 0.0


@pytest.fixture(autouse=True)
def _isolate_staging(tmp_path, monkeypatch):
    """所有測試一律把 staging 導到 tmp_path，不觸碰真實 upload_dir 旁的路徑。"""
    original_prepare = backup.prepare_staging

    def _prepare(base_dir=None):
        return original_prepare(base_dir if base_dir is not None else tmp_path / "stg")

    monkeypatch.setattr(backup, "prepare_staging", _prepare)


@pytest.fixture
def fake_settings_store(monkeypatch):
    """`settings_store.update()` 改為只操作記憶體 `_cache`（本檔測試不建 settings 表）。"""
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


@pytest.fixture
async def orchestration_db(conversations_messages_tables, monkeypatch):
    """conversations/messages 表由 conftest 共用 fixture 建立（export_db_dumps 需六表齊全），
    這裡只把 services/backup.py 的 SessionLocal 換成測試 session_maker。
    """
    session_maker, _ = conversations_messages_tables
    monkeypatch.setattr(backup, "SessionLocal", session_maker)
    return session_maker


async def _seed_document(session_maker, *, filename: str, file_path: str) -> int:
    async with session_maker() as session:
        doc_id = (
            await session.execute(
                text(
                    """
                    INSERT INTO documents (user_id, title, filename, file_path, page_count, status)
                    VALUES (1, :title, :filename, :file_path, 1, 'ready')
                    RETURNING id
                    """
                ),
                {"title": filename, "filename": filename, "file_path": file_path},
            )
        ).scalar()
        await session.commit()
    return doc_id


class _FakeGDrive:
    """記錄呼叫並模擬 Drive 資料夾/檔案狀態的假物件（不打真 API）。"""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self._folder_ids: dict[tuple[str, str | None], str] = {}
        self._next_folder = 0
        self._next_file = 0
        self.remote: dict[str, list[dict]] = {}
        self.id_to_name: dict[str, str] = {}
        self.fail_on_name: str | None = None

    async def ensure_folder(self, name: str, parent_id: str | None = None) -> str:
        key = (name, parent_id)
        if key not in self._folder_ids:
            self._next_folder += 1
            fid = f"folder-{self._next_folder}"
            self._folder_ids[key] = fid
            self.remote.setdefault(fid, [])
        self.calls.append(("ensure_folder", name, parent_id))
        return self._folder_ids[key]

    async def list_folder(self, folder_id: str) -> list[dict]:
        self.calls.append(("list_folder", folder_id))
        return [dict(f) for f in self.remote.get(folder_id, [])]

    async def upload_file(self, folder_id: str, name: str, content_or_path, mime: str) -> dict:
        self.calls.append(("upload", folder_id, name))
        if name == self.fail_on_name:
            raise gdrive.GDriveError(f"模擬上傳失敗：{name}")
        self._next_file += 1
        fid = f"file-{self._next_file}"
        self.remote.setdefault(folder_id, []).append({"id": fid, "name": name})
        self.id_to_name[fid] = name
        return {"id": fid, "name": name}

    async def update_file(self, file_id: str, content, mime: str) -> dict:
        name = self.id_to_name.get(file_id, "")
        self.calls.append(("update", file_id, name))
        if name == self.fail_on_name:
            raise gdrive.GDriveError(f"模擬覆蓋失敗：{name}")
        return {"id": file_id}

    def seed_remote(self, folder_id: str, name: str) -> str:
        self._next_file += 1
        fid = f"file-{self._next_file}"
        self.remote.setdefault(folder_id, []).append({"id": fid, "name": name})
        self.id_to_name[fid] = name
        return fid


@pytest.fixture
def fake_gdrive(monkeypatch):
    fake = _FakeGDrive()
    monkeypatch.setattr(gdrive, "ensure_folder", fake.ensure_folder)
    monkeypatch.setattr(gdrive, "list_folder", fake.list_folder)
    monkeypatch.setattr(gdrive, "upload_file", fake.upload_file)
    monkeypatch.setattr(gdrive, "update_file", fake.update_file)
    return fake


# ---------- 編排：run_backup ----------


class TestRunBackupOrchestration:
    async def test_incremental_pdf_upload_skips_existing(
        self, orchestration_db, fake_gdrive, fake_settings_store, tmp_path
    ):
        pdf_a = tmp_path / "a.pdf"
        pdf_a.write_bytes(b"A")
        pdf_b = tmp_path / "b.pdf"
        pdf_b.write_bytes(b"B")
        await _seed_document(orchestration_db, filename="a.pdf", file_path=str(pdf_a))
        await _seed_document(orchestration_db, filename="b.pdf", file_path=str(pdf_b))

        root_id = await fake_gdrive.ensure_folder(backup.BACKUP_FOLDER_NAME)
        pdfs_id = await fake_gdrive.ensure_folder("pdfs", parent_id=root_id)
        fake_gdrive.seed_remote(pdfs_id, "a.pdf")  # 遠端已有 a.pdf，應跳過
        fake_gdrive.calls.clear()

        await backup.run_backup()

        pdf_uploads = {c[2] for c in fake_gdrive.calls if c[0] == "upload" and c[1] == pdfs_id}
        assert pdf_uploads == {"b.pdf"}
        assert backup._last_run["ok"] is True

    async def test_db_json_existing_uses_update_file(
        self, orchestration_db, fake_gdrive, fake_settings_store
    ):
        root_id = await fake_gdrive.ensure_folder(backup.BACKUP_FOLDER_NAME)
        db_id = await fake_gdrive.ensure_folder("db", parent_id=root_id)
        existing_id = fake_gdrive.seed_remote(db_id, "documents.json")
        fake_gdrive.calls.clear()

        await backup.run_backup()

        update_calls = [c for c in fake_gdrive.calls if c[0] == "update" and c[1] == existing_id]
        assert len(update_calls) == 1

        other_uploads = {c[2] for c in fake_gdrive.calls if c[0] == "upload" and c[1] == db_id}
        assert other_uploads == {
            "projects.json",
            "annotations.json",
            "glossary_entries.json",
            "conversations.json",
            "messages.json",
            "settings.json",
        }
        assert backup._last_run["ok"] is True

    async def test_manifest_uploaded_last(self, orchestration_db, fake_gdrive, fake_settings_store):
        await backup.run_backup()

        assert backup._last_run["ok"] is True
        assert fake_gdrive.calls, "應有實際呼叫紀錄"
        last_call = fake_gdrive.calls[-1]
        assert last_call[0] in ("upload", "update")
        assert last_call[2] == "manifest.json"

    async def test_failure_aborts_before_manifest_and_cleans_staging(
        self, orchestration_db, fake_gdrive, fake_settings_store, tmp_path
    ):
        fake_gdrive.fail_on_name = "projects.json"

        await backup.run_backup()

        assert backup._last_run["ok"] is False
        assert isinstance(backup._last_run["error"], str)
        assert backup._last_run["error"]  # 非空
        assert not any(
            c[2] == "manifest.json" for c in fake_gdrive.calls if c[0] in ("upload", "update")
        )
        # staging 目錄（本檔固定導到 tmp_path/"stg"，見 _isolate_staging）失敗後應已清除
        assert not (tmp_path / "stg").exists()
        assert backup._progress is None

    async def test_gdrive_disconnected_records_reconnect_hint(
        self, orchestration_db, fake_settings_store, monkeypatch
    ):
        async def _raise_disconnected(name, parent_id=None):
            raise gdrive.GDriveDisconnectedError()

        monkeypatch.setattr(gdrive, "ensure_folder", _raise_disconnected)

        await backup.run_backup()

        assert backup._last_run["ok"] is False
        assert "重新連接" in backup._last_run["error"]


class TestConcurrencyGuard:
    async def test_second_call_is_noop_while_first_running(
        self, orchestration_db, fake_settings_store, monkeypatch
    ):
        gate = asyncio.Event()
        call_count = {"ensure_folder": 0}

        async def _ensure_folder(name, parent_id=None):
            call_count["ensure_folder"] += 1
            if name == backup.BACKUP_FOLDER_NAME:
                await gate.wait()
            return f"folder-{name}-{parent_id}"

        async def _list_folder(folder_id):
            return []

        async def _upload_file(folder_id, name, content_or_path, mime):
            return {"id": "f", "name": name}

        async def _update_file(file_id, content, mime):
            return {"id": file_id}

        monkeypatch.setattr(gdrive, "ensure_folder", _ensure_folder)
        monkeypatch.setattr(gdrive, "list_folder", _list_folder)
        monkeypatch.setattr(gdrive, "upload_file", _upload_file)
        monkeypatch.setattr(gdrive, "update_file", _update_file)

        assert backup.is_running() is False
        task = asyncio.create_task(backup.run_backup())
        await asyncio.sleep(0)  # 讓第一次呼叫跑到 ensure_folder(root) 卡住
        assert backup.is_running() is True

        # 併發呼叫第二次：non-blocking 直接 return，不再呼叫 ensure_folder
        await backup.run_backup()
        assert call_count["ensure_folder"] == 1

        gate.set()
        await task
        assert backup.is_running() is False
        assert backup._last_run["ok"] is True


# ---------- get_status ----------


class TestGetStatus:
    async def test_connected_true_when_refresh_token_present(self, fake_settings_store):
        fake_settings_store["gdrive_refresh_token"] = "rtoken"
        status = await backup.get_status()
        assert status["connected"] is True
        assert status["running"] is False
        assert status["progress"] is None

    async def test_connected_false_when_no_refresh_token(self, fake_settings_store):
        status = await backup.get_status()
        assert status["connected"] is False

    async def test_last_run_falls_back_to_persisted_value_on_cold_start(self, fake_settings_store):
        fake_settings_store["backup_last_run"] = {
            "at": "2026-01-01T00:00:00",
            "ok": True,
            "counts": {},
        }
        backup._last_run = None  # 冷啟動：記憶體內尚無本次執行結果
        status = await backup.get_status()
        assert status["last_run"] == {"at": "2026-01-01T00:00:00", "ok": True, "counts": {}}

    async def test_last_run_prefers_in_memory_value_over_persisted(self, fake_settings_store):
        fake_settings_store["backup_last_run"] = {"at": "old", "ok": True}
        backup._last_run = {"at": "new", "ok": False, "error": "x"}
        status = await backup.get_status()
        assert status["last_run"]["at"] == "new"

    async def test_interval_hours_defaults_to_zero(self, fake_settings_store):
        status = await backup.get_status()
        assert status["interval_hours"] == 0

    async def test_interval_hours_reflects_setting(self, fake_settings_store):
        fake_settings_store["backup_interval_hours"] = 6
        status = await backup.get_status()
        assert status["interval_hours"] == 6


# ---------- routers/backup.py 端點 ----------


class TestStatusEndpoint:
    async def test_status_endpoint_shape(self, async_client, fake_settings_store):
        fake_settings_store["gdrive_refresh_token"] = "rtoken"
        resp = await async_client.get("/api/backup/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["connected"] is True
        assert body["running"] is False
        assert "progress" in body
        assert "last_run" in body
        assert "interval_hours" in body


class TestRunEndpoint:
    async def test_returns_202_when_connected_and_idle(
        self, async_client, fake_settings_store, monkeypatch
    ):
        fake_settings_store["gdrive_refresh_token"] = "rtoken"
        called = {"n": 0}

        async def _noop():
            called["n"] += 1

        monkeypatch.setattr(backup, "run_backup", _noop)
        resp = await async_client.post(
            "/api/backup/run", headers={"Content-Type": "application/json"}
        )
        assert resp.status_code == 202
        assert resp.json() == {"started": True}
        assert called["n"] == 1

    async def test_returns_400_when_not_connected(self, async_client, fake_settings_store):
        resp = await async_client.post(
            "/api/backup/run", headers={"Content-Type": "application/json"}
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "not_connected"

    async def test_returns_409_when_already_running(
        self, async_client, fake_settings_store, monkeypatch
    ):
        fake_settings_store["gdrive_refresh_token"] = "rtoken"
        monkeypatch.setattr(backup, "is_running", lambda: True)
        resp = await async_client.post(
            "/api/backup/run", headers={"Content-Type": "application/json"}
        )
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "backup_running"


class TestAuthStartEndpoint:
    async def test_returns_auth_url(self, async_client, fake_settings_store):
        fake_settings_store["gdrive_client_id"] = "cid"
        resp = await async_client.get("/api/backup/auth/start")
        assert resp.status_code == 200
        assert resp.json()["auth_url"].startswith("https://accounts.google.com")

    async def test_missing_client_id_returns_400(self, async_client, fake_settings_store):
        resp = await async_client.get("/api/backup/auth/start")
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "client_id_unset"


class TestAuthCallbackEndpoint:
    async def test_success_stores_refresh_token_and_shows_html(
        self, async_client, fake_settings_store, monkeypatch
    ):
        async def _fake_exchange(code, state):
            assert code == "auth-code"
            assert state == "st"
            return "new-refresh-token"

        monkeypatch.setattr(backup_router, "exchange_code", _fake_exchange)
        resp = await async_client.get(
            "/api/backup/auth/callback", params={"code": "auth-code", "state": "st"}
        )
        assert resp.status_code == 200
        assert "已連接" in resp.text
        assert fake_settings_store["gdrive_refresh_token"] == "new-refresh-token"

    async def test_state_mismatch_returns_failure_html(self, async_client, fake_settings_store):
        # 瀏覽器端點一律回 HTML：state 不符走失敗頁，不回裸 JSON
        resp = await async_client.get(
            "/api/backup/auth/callback", params={"code": "c", "state": "unknown-state"}
        )
        assert resp.status_code == 400
        assert resp.headers["content-type"].startswith("text/html")
        assert "連接失敗" in resp.text
        assert "gdrive_refresh_token" not in fake_settings_store

    async def test_exchange_failure_returns_failure_html(
        self, async_client, fake_settings_store, monkeypatch
    ):
        # exchange 失敗（如 Google 未回 refresh_token）也統一渲染失敗頁，訊息經 escape
        async def _fake_exchange(code, state):
            raise gdrive.GDriveError("Google 未回傳 refresh_token（<test>）")

        monkeypatch.setattr(backup_router, "exchange_code", _fake_exchange)
        resp = await async_client.get(
            "/api/backup/auth/callback", params={"code": "c", "state": "st"}
        )
        assert resp.status_code == 400
        assert resp.headers["content-type"].startswith("text/html")
        assert "連接失敗" in resp.text
        assert "&lt;test&gt;" in resp.text  # html.escape 生效
        assert "gdrive_refresh_token" not in fake_settings_store

    async def test_google_error_param_returns_failure_html(self, async_client, fake_settings_store):
        resp = await async_client.get(
            "/api/backup/auth/callback", params={"error": "access_denied"}
        )
        assert resp.status_code == 400
        assert "連接失敗" in resp.text
        assert "access_denied" in resp.text
        assert "gdrive_refresh_token" not in fake_settings_store


class TestDisconnectEndpoint:
    async def test_disconnect_clears_refresh_token(self, async_client, fake_settings_store):
        fake_settings_store["gdrive_refresh_token"] = "rtoken"
        gdrive._access_token = "still-valid-access"  # 模擬記憶體中尚有效的 access token
        gdrive._access_expires_at = 9e9
        resp = await async_client.post(
            "/api/backup/auth/disconnect", headers={"Content-Type": "application/json"}
        )
        assert resp.status_code == 204
        assert "gdrive_refresh_token" not in fake_settings_store
        # 防禦縱深：disconnect 同時清除記憶體 access token 快取
        assert gdrive._access_token is None
