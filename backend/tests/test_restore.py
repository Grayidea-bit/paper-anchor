"""還原服務 + 端點測試（M13 D11 / T-RS-01）。

gdrive 與 ingest_document 全 mock（不打真 API、不真解析 PDF）；沿用 conftest 的 SQLite
記憶體 DB，並補上 conversations/messages 表（同 test_backup_export.py 手法）。settings_store
以記憶體版 update 取代（不建 settings 表）。

十項覆蓋（見 T-RS-01 卡）：空庫全還原、重疊冪等、annotations 新舊比對、chunk_id NULL 無
FK violation、citations remap 與查無→null、ingest 觸發參數、單篇 ingest 失敗續跑、409 互斥、
failed doc 修復路徑、download_file MockTransport（正常/429/401）。
"""

import json
import os
import time
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import text

from app import settings_store
from app.routers import backup as backup_router
from app.services import backup, gdrive, restore

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeDrive:
    """模擬 Drive 資料夾/檔案：ensure_folder 決定性 id、download_file 依 id 寫檔落地。"""

    def __init__(self) -> None:
        self.folders: dict[tuple[str, str | None], str] = {}
        self.files: dict[str, list[dict]] = {}
        self.content: dict[str, bytes] = {}
        self.download_calls: list[str] = []
        self._n = 0

    async def ensure_folder(self, name: str, parent_id: str | None = None) -> str:
        key = (name, parent_id)
        if key not in self.folders:
            self._n += 1
            fid = f"fld-{self._n}"
            self.folders[key] = fid
            self.files.setdefault(fid, [])
        return self.folders[key]

    async def list_folder(self, folder_id: str) -> list[dict]:
        return [dict(f) for f in self.files.get(folder_id, [])]

    async def download_file(self, file_id: str, dest_path) -> None:
        self.download_calls.append(file_id)
        with open(os.fspath(dest_path), "wb") as f:  # noqa: ASYNC230  # 測試假物件
            f.write(self.content[file_id])

    def add_file(self, folder_id: str, name: str, content: bytes) -> str:
        self._n += 1
        fid = f"file-{self._n}"
        self.files.setdefault(folder_id, []).append({"id": fid, "name": name})
        self.content[fid] = content
        return fid

    async def push_backup(self, *, dumps: dict, pdfs: dict, format_version: int = 1) -> None:
        root = await self.ensure_folder(backup.BACKUP_FOLDER_NAME)
        db = await self.ensure_folder("db", parent_id=root)
        pdfs_id = await self.ensure_folder("pdfs", parent_id=root)
        manifest = {
            "format_version": format_version,
            "counts": {},
            "pdfs": [{"name": n} for n in pdfs],
        }
        self.add_file(root, "manifest.json", json.dumps(manifest).encode("utf-8"))
        for table, rows in dumps.items():
            self.add_file(db, f"{table}.json", json.dumps(rows).encode("utf-8"))
        for name, content in pdfs.items():
            self.add_file(pdfs_id, name, content)


class FakeIngest:
    """記錄 (doc_id, run_digest)；預設把文獻標成 ready（模擬成功），可指定第幾次呼叫失敗。"""

    def __init__(self, session_maker) -> None:
        self.calls: list[tuple[int, bool]] = []
        self.fail_call_indices: set[int] = set()
        self._session_maker = session_maker

    async def __call__(self, doc_id: int, run_digest: bool = True) -> None:
        self.calls.append((doc_id, run_digest))
        if len(self.calls) in self.fail_call_indices:
            raise RuntimeError("simulated ingest failure")
        async with self._session_maker() as s:
            await s.execute(
                text("UPDATE documents SET status = 'ready' WHERE id = :id"), {"id": doc_id}
            )
            await s.commit()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def restore_env(test_db, tmp_path, monkeypatch):
    """補 conversations/messages 表、接管 SessionLocal/settings/gdrive/ingest/upload_dir。"""
    session_maker, engine = test_db
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
                    project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
                    scope TEXT NOT NULL DEFAULT 'document',
                    title TEXT NOT NULL DEFAULT '新對話',
                    model TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    citations JSON NOT NULL DEFAULT '[]',
                    selection JSON,
                    token_usage JSON NOT NULL DEFAULT '{}',
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )

    monkeypatch.setattr(restore, "SessionLocal", session_maker)
    monkeypatch.setattr(
        restore, "get_settings", lambda: SimpleNamespace(upload_dir=str(tmp_path / "uploads"))
    )

    # settings_store：記憶體版 update（不建 settings 表）。
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

    # backup 模組級還原狀態重置。
    monkeypatch.setattr(backup, "_last_restore", None)
    monkeypatch.setattr(backup, "_progress", None)
    monkeypatch.setattr(backup, "_operation", None)

    fake = FakeDrive()
    monkeypatch.setattr(gdrive, "ensure_folder", fake.ensure_folder)
    monkeypatch.setattr(gdrive, "list_folder", fake.list_folder)
    monkeypatch.setattr(gdrive, "download_file", fake.download_file)

    fake_ingest = FakeIngest(session_maker)
    monkeypatch.setattr(restore, "ingest_document", fake_ingest)

    return SimpleNamespace(
        session_maker=session_maker, drive=fake, ingest=fake_ingest, tmp_path=tmp_path
    )


# 便捷建構 dump 列 -----------------------------------------------------------

_TS = "2026-03-01T00:00:00+00:00"


def _doc(doc_id, *, file_path, filename="paper.pdf", title="Paper", digest=None, project_id=None):
    return {
        "id": doc_id,
        "user_id": 1,
        "project_id": project_id,
        "title": title,
        "filename": filename,
        "file_path": file_path,
        "page_count": 3,
        "status": "ready",
        "error_msg": None,
        "digest": digest,
        "token_usage": {},
        "created_at": _TS,
    }


def _empty_dumps():
    return {
        "projects": [],
        "documents": [],
        "annotations": [],
        "glossary_entries": [],
        "conversations": [],
        "messages": [],
    }


async def _count(session_maker, sql, params=None):
    async with session_maker() as s:
        return (await s.execute(text(sql), params or {})).scalar()


# ---------------------------------------------------------------------------
# 1. 空庫全還原
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_db_full_restore(restore_env):
    env = restore_env
    dumps = _empty_dumps()
    dumps["projects"] = [{"id": 901, "user_id": 1, "name": "Proj A", "created_at": _TS}]
    dumps["documents"] = [
        _doc(801, file_path="/data/uploads/uuidA.pdf", project_id=901),
        _doc(802, file_path="/data/uploads/uuidB.pdf"),
    ]
    await env.drive.push_backup(dumps=dumps, pdfs={"uuidA.pdf": b"%PDF-A", "uuidB.pdf": b"%PDF-B"})

    await restore.run_restore()

    result = backup._last_restore
    assert result["ok"] is True
    assert result["summary"]["documents_new"] == 2
    assert result["summary"]["documents_skipped"] == 0
    # 兩篇都排入 ingest（無 digest → run_digest True）
    assert len(env.ingest.calls) == 2
    assert all(run_digest is True for _, run_digest in env.ingest.calls)

    # created_at 保留、id 全新（不等於 dump 端 id）
    async with env.session_maker() as s:
        rows = (
            await s.execute(
                text("SELECT id, created_at FROM documents WHERE filename = 'paper.pdf'")
            )
        ).all()
    assert len(rows) == 2
    for row in rows:
        assert row.id not in (801, 802)
        assert str(row.created_at) == _TS
    proj_created = await _count(
        env.session_maker, "SELECT created_at FROM projects WHERE name = 'Proj A'"
    )
    assert str(proj_created) == _TS


# ---------------------------------------------------------------------------
# 2. 重疊冪等：跑兩次結果相同、同 UUID 文獻 ingest 零呼叫
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_idempotent_second_run(restore_env):
    env = restore_env
    dumps = _empty_dumps()
    dumps["projects"] = [{"id": 901, "user_id": 1, "name": "Proj A", "created_at": _TS}]
    dumps["documents"] = [_doc(801, file_path="/data/uploads/uuidA.pdf", project_id=901)]
    dumps["annotations"] = [
        {
            "id": 701,
            "document_id": 801,
            "type": "highlight",
            "color": "amber",
            "page": 1,
            "bbox_list": [[0, 0, 10, 10]],
            "chunk_id": 55,
            "selected_text": "sel",
            "note_text": "n",
            "created_at": _TS,
            "updated_at": _TS,
        }
    ]
    dumps["glossary_entries"] = [
        {
            "id": 601,
            "document_id": 801,
            "term": "t",
            "translation": "tr",
            "target_lang": "繁體中文",
            "page": 1,
            "bbox_list": [[0, 0, 5, 5]],
            "chunk_id": 55,
            "notes": "",
            "created_at": _TS,
        }
    ]
    dumps["conversations"] = [
        {
            "id": 501,
            "scope": "document",
            "document_id": 801,
            "project_id": None,
            "title": "conv",
            "model": "opus",
            "created_at": _TS,
        }
    ]
    dumps["messages"] = [
        {
            "id": 401,
            "conversation_id": 501,
            "role": "user",
            "content": "hi",
            "citations": [],
            "selection": None,
            "token_usage": {},
            "created_at": _TS,
        }
    ]
    await env.drive.push_backup(dumps=dumps, pdfs={"uuidA.pdf": b"%PDF-A"})

    await restore.run_restore()
    first = backup._last_restore["summary"]
    assert first["documents_new"] == 1
    assert first["annotations_new"] == 1
    assert first["glossary_new"] == 1
    assert first["conversations_new"] == 1
    assert first["messages_new"] == 1
    assert len(env.ingest.calls) == 1

    env.ingest.calls.clear()
    await restore.run_restore()
    second = backup._last_restore["summary"]
    assert second == {
        "documents_new": 0,
        "documents_skipped": 0,
        "annotations_new": 0,
        "annotations_updated": 0,
        "glossary_new": 0,
        "conversations_new": 0,
        "messages_new": 0,
        "ingest_failed": [],
    }
    # 同 UUID 文獻整篇跳過 → ingest 零呼叫
    assert env.ingest.calls == []
    # 未重複插入
    assert await _count(env.session_maker, "SELECT COUNT(*) FROM annotations") == 1
    assert await _count(env.session_maker, "SELECT COUNT(*) FROM conversations") == 1
    assert await _count(env.session_maker, "SELECT COUNT(*) FROM messages") == 1


# ---------------------------------------------------------------------------
# 3. annotations 備份較新覆蓋 / 本地較新保留
# ---------------------------------------------------------------------------


async def _seed_local_doc_with_annotation(session_maker, *, uuid_name, ann_updated_at):
    async with session_maker() as s:
        doc_id = (
            await s.execute(
                text(
                    """
                    INSERT INTO documents (user_id, title, filename, file_path, page_count, status)
                    VALUES (1, 'Local', 'paper.pdf', :fp, 3, 'ready')
                    RETURNING id
                    """
                ),
                {"fp": f"/data/uploads/{uuid_name}"},
            )
        ).scalar()
        ann_id = (
            await s.execute(
                text(
                    """
                    INSERT INTO annotations
                        (document_id, type, color, page, bbox_list, selected_text, note_text,
                         created_at, updated_at)
                    VALUES (:did, 'highlight', 'amber', 1, :bbox, 'old sel', 'old note',
                            :ts, :ts)
                    RETURNING id
                    """
                ),
                {"did": doc_id, "bbox": json.dumps([[0, 0, 10, 10]]), "ts": ann_updated_at},
            )
        ).scalar()
        await s.commit()
    return doc_id, ann_id


def _ann_dump(document_id, *, updated_at, note="new note", color="sage"):
    return {
        "id": 701,
        "document_id": document_id,
        "type": "highlight",
        "color": color,
        "page": 1,
        "bbox_list": [[0, 0, 10, 10]],
        "chunk_id": 999,
        "selected_text": "new sel",
        "note_text": note,
        "created_at": _TS,
        "updated_at": updated_at,
    }


@pytest.mark.asyncio
async def test_annotation_backup_newer_overwrites(restore_env):
    env = restore_env
    doc_id, ann_id = await _seed_local_doc_with_annotation(
        env.session_maker, uuid_name="uuidA.pdf", ann_updated_at="2026-01-01T00:00:00+00:00"
    )
    dumps = _empty_dumps()
    dumps["documents"] = [_doc(801, file_path="/data/uploads/uuidA.pdf")]
    dumps["annotations"] = [_ann_dump(801, updated_at="2026-06-01T00:00:00+00:00")]
    await env.drive.push_backup(dumps=dumps, pdfs={})  # 已存在文獻，不需遠端 PDF

    await restore.run_restore()

    summary = backup._last_restore["summary"]
    assert summary["annotations_updated"] == 1
    assert summary["annotations_new"] == 0
    async with env.session_maker() as s:
        row = (
            await s.execute(
                text("SELECT note_text, color FROM annotations WHERE id = :id"), {"id": ann_id}
            )
        ).one()
    assert row.note_text == "new note"
    assert row.color == "sage"


@pytest.mark.asyncio
async def test_annotation_local_newer_kept(restore_env):
    env = restore_env
    doc_id, ann_id = await _seed_local_doc_with_annotation(
        env.session_maker, uuid_name="uuidA.pdf", ann_updated_at="2026-06-01T00:00:00+00:00"
    )
    dumps = _empty_dumps()
    dumps["documents"] = [_doc(801, file_path="/data/uploads/uuidA.pdf")]
    dumps["annotations"] = [_ann_dump(801, updated_at="2026-01-01T00:00:00+00:00")]
    await env.drive.push_backup(dumps=dumps, pdfs={})

    await restore.run_restore()

    summary = backup._last_restore["summary"]
    assert summary["annotations_updated"] == 0
    async with env.session_maker() as s:
        row = (
            await s.execute(
                text("SELECT note_text FROM annotations WHERE id = :id"), {"id": ann_id}
            )
        ).one()
    assert row.note_text == "old note"  # 本地較新，未被覆蓋


# ---------------------------------------------------------------------------
# 4. chunk_id 一律 NULL 無 FK violation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chunk_id_always_null(restore_env):
    env = restore_env
    dumps = _empty_dumps()
    dumps["documents"] = [_doc(801, file_path="/data/uploads/uuidA.pdf")]
    dumps["annotations"] = [
        {
            "id": 701,
            "document_id": 801,
            "type": "note",
            "color": "amber",
            "page": 2,
            "bbox_list": [[1, 1, 9, 9]],
            "chunk_id": 12345,  # 備份端舊 id，必不可沿用
            "selected_text": "s",
            "note_text": "n",
            "created_at": _TS,
            "updated_at": _TS,
        }
    ]
    dumps["glossary_entries"] = [
        {
            "id": 601,
            "document_id": 801,
            "term": "t",
            "translation": "tr",
            "target_lang": "繁體中文",
            "page": 2,
            "bbox_list": [[1, 1, 5, 5]],
            "chunk_id": 67890,
            "notes": "",
            "created_at": _TS,
        }
    ]
    await env.drive.push_backup(dumps=dumps, pdfs={"uuidA.pdf": b"%PDF"})

    await restore.run_restore()  # 若沿用舊 chunk_id 會 FK violation

    assert backup._last_restore["ok"] is True
    ann_chunk = await _count(env.session_maker, "SELECT chunk_id FROM annotations LIMIT 1")
    glo_chunk = await _count(env.session_maker, "SELECT chunk_id FROM glossary_entries LIMIT 1")
    assert ann_chunk is None
    assert glo_chunk is None


# ---------------------------------------------------------------------------
# 5. citations document_id remap 與查無→null（label/chunk_id 原樣）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_citations_document_id_remap(restore_env):
    env = restore_env
    dumps = _empty_dumps()
    # docA 新增（有 PDF）；docB 缺 PDF → 跳過（citations 對它查無）
    dumps["documents"] = [
        _doc(801, file_path="/data/uploads/uuidA.pdf"),
        _doc(802, file_path="/data/uploads/uuidB.pdf"),
    ]
    dumps["conversations"] = [
        {
            "id": 501,
            "scope": "library",
            "document_id": None,
            "project_id": None,
            "title": "lib conv",
            "model": None,
            "created_at": _TS,
        }
    ]
    dumps["messages"] = [
        {
            "id": 401,
            "conversation_id": 501,
            "role": "assistant",
            "content": "answer",
            "citations": [
                {"label": "1", "chunk_id": 5, "chunk_index": 2, "page": 3, "document_id": 801},
                {"label": "2", "chunk_id": 6, "chunk_index": 4, "page": 7, "document_id": 802},
                {"label": "3", "chunk_id": 7, "chunk_index": 9, "page": 1, "document_id": 99999},
            ],
            "selection": None,
            "token_usage": {},
            "created_at": _TS,
        }
    ]
    await env.drive.push_backup(dumps=dumps, pdfs={"uuidA.pdf": b"%PDF-A"})  # 無 uuidB

    await restore.run_restore()

    assert backup._last_restore["summary"]["documents_skipped"] == 1  # docB 缺 PDF
    async with env.session_maker() as s:
        local_a = (
            await s.execute(text("SELECT id FROM documents WHERE file_path LIKE '%uuidA.pdf'"))
        ).scalar()
        raw = (await s.execute(text("SELECT citations FROM messages LIMIT 1"))).scalar()
    cits = json.loads(raw) if isinstance(raw, str) else raw
    assert cits[0]["document_id"] == local_a  # remap 命中
    assert cits[0]["label"] == "1" and cits[0]["chunk_id"] == 5  # 其餘欄位原樣
    assert cits[1]["document_id"] is None  # docB 跳過 → null
    assert cits[2]["document_id"] is None  # 查無 → null
    assert cits[2]["chunk_id"] == 7  # chunk_id 原樣（不隨 document_id 一起清）


# ---------------------------------------------------------------------------
# 6. ingest 觸發參數：dump 有 digest → run_digest=False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_run_digest_flag(restore_env):
    env = restore_env
    dumps = _empty_dumps()
    dumps["documents"] = [
        _doc(801, file_path="/data/uploads/withdigest.pdf", digest={"tldr": "x", "sections": []}),
        _doc(802, file_path="/data/uploads/nodigest.pdf", digest=None),
    ]
    await env.drive.push_backup(
        dumps=dumps, pdfs={"withdigest.pdf": b"%PDF-1", "nodigest.pdf": b"%PDF-2"}
    )

    await restore.run_restore()

    # 依 file_path 對回 doc → run_digest
    async with env.session_maker() as s:
        rows = (
            await s.execute(
                text("SELECT id, file_path FROM documents WHERE filename = 'paper.pdf'")
            )
        ).all()
    id_by_uuid = {os.path.basename(r.file_path): r.id for r in rows}
    flags = dict(env.ingest.calls)  # doc_id -> run_digest
    assert flags[id_by_uuid["withdigest.pdf"]] is False  # dump 有 digest → 跳過重生成
    assert flags[id_by_uuid["nodigest.pdf"]] is True


# ---------------------------------------------------------------------------
# 7. 單篇 ingest 失敗續跑 + summary.ingest_failed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_ingest_failure_continues(restore_env):
    env = restore_env
    env.ingest.fail_call_indices = {1}  # 第一次 ingest 失敗
    dumps = _empty_dumps()
    dumps["documents"] = [
        _doc(801, file_path="/data/uploads/uuidA.pdf", title="Paper A"),
        _doc(802, file_path="/data/uploads/uuidB.pdf", title="Paper B"),
    ]
    await env.drive.push_backup(dumps=dumps, pdfs={"uuidA.pdf": b"%PDF-A", "uuidB.pdf": b"%PDF-B"})

    await restore.run_restore()

    result = backup._last_restore
    assert result["ok"] is True  # 整輪不中止
    assert len(env.ingest.calls) == 2  # 兩篇都嘗試過
    assert result["summary"]["ingest_failed"] == ["Paper A"]
    assert result["summary"]["documents_new"] == 2


# ---------------------------------------------------------------------------
# 8. 409 互斥（restore 進行中 POST /run 與 /restore 都 409）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_409_mutual_exclusion(async_client, monkeypatch):
    monkeypatch.setattr(settings_store, "_cache", {"gdrive_refresh_token": "rtoken"})
    monkeypatch.setattr(backup, "is_running", lambda: True)

    run_resp = await async_client.post("/api/backup/run")
    restore_resp = await async_client.post("/api/backup/restore")

    assert run_resp.status_code == 409
    assert restore_resp.status_code == 409
    assert restore_resp.json()["error"]["code"] == "operation_running"


@pytest.mark.asyncio
async def test_restore_not_connected_returns_400(async_client, monkeypatch):
    monkeypatch.setattr(settings_store, "_cache", {})
    resp = await async_client.post("/api/backup/restore")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "not_connected"


@pytest.mark.asyncio
async def test_restore_returns_202_and_schedules(async_client, monkeypatch):
    monkeypatch.setattr(settings_store, "_cache", {"gdrive_refresh_token": "rtoken"})
    monkeypatch.setattr(backup, "is_running", lambda: False)
    called = {"n": 0}

    async def _noop() -> None:
        called["n"] += 1

    monkeypatch.setattr(backup_router.restore, "run_restore", _noop)
    resp = await async_client.post("/api/backup/restore")
    assert resp.status_code == 202
    assert resp.json() == {"started": True}
    assert called["n"] == 1


# ---------------------------------------------------------------------------
# 9. failed doc 修復路徑（delete_chunks 被呼叫）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_doc_repair_path(restore_env):
    env = restore_env
    # 本地已存在同 UUID 文獻，status='failed'，且留有殘塊
    async with env.session_maker() as s:
        doc_id = (
            await s.execute(
                text(
                    """
                    INSERT INTO documents (user_id, title, filename, file_path, page_count, status)
                    VALUES (1, 'Broken', 'paper.pdf', '/data/uploads/uuidA.pdf', 3, 'failed')
                    RETURNING id
                    """
                )
            )
        ).scalar()
        await s.execute(
            text(
                """
                INSERT INTO chunks (document_id, chunk_index, page, content, bbox_list)
                VALUES (:d, 0, 1, 'stale', '[]')
                """
            ),
            {"d": doc_id},
        )
        await s.commit()

    dumps = _empty_dumps()
    dumps["documents"] = [_doc(801, file_path="/data/uploads/uuidA.pdf", title="Paper A")]
    await env.drive.push_backup(dumps=dumps, pdfs={})  # 已存在，走修復不需遠端 PDF

    await restore.run_restore()

    # delete_chunks 被呼叫 → 殘塊清空；且該文獻被排入重嵌
    assert (
        await _count(
            env.session_maker, "SELECT COUNT(*) FROM chunks WHERE document_id = :d", {"d": doc_id}
        )
        == 0
    )
    assert doc_id in [c[0] for c in env.ingest.calls]
    # 非新文獻
    assert backup._last_restore["summary"]["documents_new"] == 0


# ---------------------------------------------------------------------------
# 10. download_file MockTransport（正常 / 429 退避 / 401 刷新）
# ---------------------------------------------------------------------------

_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _install_transport(monkeypatch, handler):
    transport = httpx.MockTransport(handler)

    def factory(*args, **kwargs):
        kwargs.pop("transport", None)
        return _REAL_ASYNC_CLIENT(*args, transport=transport, **kwargs)

    monkeypatch.setattr(gdrive.httpx, "AsyncClient", factory)


@pytest.fixture
def gdrive_transport(monkeypatch):
    monkeypatch.setattr(
        settings_store,
        "_cache",
        {
            "gdrive_client_id": "cid",
            "gdrive_client_secret": "csecret",
            "gdrive_refresh_token": "rtoken",
        },
    )
    gdrive._access_token = "cached-access"
    gdrive._access_expires_at = time.monotonic() + 3600

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(gdrive.asyncio, "sleep", _no_sleep)
    yield
    gdrive._access_token = None
    gdrive._access_expires_at = 0.0


@pytest.mark.asyncio
async def test_download_file_writes_content(gdrive_transport, monkeypatch, tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert "alt=media" in str(request.url)
        assert "file-xyz" in str(request.url)
        return httpx.Response(200, content=b"PDF-BYTES-CONTENT")

    _install_transport(monkeypatch, handler)
    dest = tmp_path / "out.pdf"
    await gdrive.download_file("file-xyz", dest)
    assert dest.read_bytes() == b"PDF-BYTES-CONTENT"


@pytest.mark.asyncio
async def test_download_file_retries_on_429(gdrive_transport, monkeypatch, tmp_path):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"error": "rate"})
        return httpx.Response(200, content=b"AFTER-RETRY")

    _install_transport(monkeypatch, handler)
    dest = tmp_path / "out.pdf"
    await gdrive.download_file("fid", dest)
    assert calls["n"] == 2
    assert dest.read_bytes() == b"AFTER-RETRY"


@pytest.mark.asyncio
async def test_download_file_refreshes_on_401(gdrive_transport, monkeypatch, tmp_path):
    gdrive._access_token = "stale"
    gdrive._access_expires_at = time.monotonic() + 3600
    seq: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == gdrive.TOKEN_URI:
            seq.append("token")
            return httpx.Response(200, json={"access_token": "fresh", "expires_in": 3600})
        auth = request.headers["Authorization"]
        seq.append(auth)
        if auth == "Bearer stale":
            return httpx.Response(401, json={"error": "unauth"})
        return httpx.Response(200, content=b"FRESH-DOWNLOAD")

    _install_transport(monkeypatch, handler)
    dest = tmp_path / "out.pdf"
    await gdrive.download_file("fid", dest)
    assert seq == ["Bearer stale", "token", "Bearer fresh"]
    assert dest.read_bytes() == b"FRESH-DOWNLOAD"
