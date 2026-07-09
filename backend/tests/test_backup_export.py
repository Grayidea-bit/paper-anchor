"""備份匯出層測試（M12 D10 / T-BK-02）：db dump + manifest v1 + 秘密不外洩。"""

import json
from pathlib import Path

import pytest
from sqlalchemy import text

from app import settings_store
from app.config import get_settings
from app.db import repo
from app.services import backup


def _maybe_json(value):
    """SQLite 測試 DB 的 JSON 欄位回傳原始字串（不像 Postgres JSONB 會自動解析）；
    嘗試 json.loads，失敗就原樣回傳（同 tests/test_repo.py 既有作法）。
    """
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value
    return value


@pytest.fixture
async def backup_db(conversations_messages_tables, monkeypatch):
    """在既有 test_db（users/projects/documents/chunks/annotations/glossary_entries）
    與 conftest 共用 fixture 補上的 conversations / messages 表之上，種入最小資料集，
    並把 services/backup.py 的 SessionLocal 換成測試用 session_maker。
    """
    session_maker, _ = conversations_messages_tables

    monkeypatch.setattr(backup, "SessionLocal", session_maker)
    # settings_store：直接灌 cache，略過 DB 的 settings 表（本卡不需要它，
    # ensure_loaded() 見快取非 None 即直接回傳，不會碰 DB）。
    monkeypatch.setattr(
        settings_store,
        "_cache",
        {
            "llm_api_key": "nvapi-SUPER-SECRET-VALUE",
            "claude_oauth_token": "sk-ant-oat-SECRET-TOKEN",
            "llm_chat_model": "deepseek-ai/deepseek-v4-flash",
            "translation_target_lang": "English",
        },
    )

    async with session_maker() as session:
        # project
        project_id = (
            await session.execute(
                text("INSERT INTO projects (user_id, name) VALUES (1, 'Demo Project') RETURNING id")
            )
        ).scalar()

        # 第二篇文獻：file_path 指向真實存在的檔案（供 manifest pdfs 清單命中）。
        # test_db 既有的 document(id=1, file_path='/tmp/test.pdf') 該路徑在本機不存在，
        # 剛好覆蓋「檔案遺失時跳過並記 log warning」的分支。
        second_doc_id = (
            await session.execute(
                text(
                    """
                    INSERT INTO documents
                        (user_id, project_id, title, filename, file_path, page_count, status)
                    VALUES (1, :pid, 'Second Paper', 'second.pdf', :file_path, 3, 'ready')
                    RETURNING id
                    """
                ),
                {"pid": project_id, "file_path": "__PLACEHOLDER__"},
            )
        ).scalar()

        # annotation + glossary entry：掛在既有 chunk（doc_id=1, chunk_id=1）上。
        await session.execute(
            text(
                """
                INSERT INTO annotations
                    (document_id, type, color, page, bbox_list, chunk_id, selected_text, note_text)
                VALUES (1, 'highlight', 'amber', 1, :bbox, 1, 'selected text', 'a note')
                """
            ),
            {"bbox": json.dumps([[0, 0, 10, 10]])},
        )
        await session.execute(
            text(
                """
                INSERT INTO glossary_entries
                    (document_id, term, translation, target_lang, page, bbox_list, chunk_id, notes)
                VALUES (1, 'term', 'translation', 'English', 1, :bbox, 1, 'note')
                """
            ),
            {"bbox": json.dumps([[0, 0, 10, 10]])},
        )

        conv_id = (
            await session.execute(
                text(
                    """
                    INSERT INTO conversations (document_id, project_id, scope, title)
                    VALUES (1, NULL, 'document', 'conv title')
                    RETURNING id
                    """
                )
            )
        ).scalar()
        await session.execute(
            text(
                """
                INSERT INTO messages
                    (conversation_id, role, content, citations, selection, token_usage)
                VALUES (:conv_id, 'user', 'hello', '[]', NULL, '{}')
                """
            ),
            {"conv_id": conv_id},
        )
        await session.commit()

    return {
        "session_maker": session_maker,
        "second_doc_id": second_doc_id,
        "project_id": project_id,
    }


@pytest.mark.asyncio
class TestExportDbDumps:
    async def test_writes_all_seven_json_files(self, backup_db, tmp_path):
        staging = backup.prepare_staging(tmp_path / "stg")
        await backup.export_db_dumps(staging)

        db_dir = staging / "db"
        expected_files = {f"{t}.json" for t in backup.DUMP_TABLES} | {"settings.json"}
        actual_files = {p.name for p in db_dir.glob("*.json")}
        assert actual_files == expected_files

    async def test_counts_match_seeded_rows(self, backup_db, tmp_path):
        staging = backup.prepare_staging(tmp_path / "stg")
        counts = await backup.export_db_dumps(staging)

        assert counts == {
            "documents": 2,  # conftest 的 1 篇 + 本 fixture 的 1 篇
            "projects": 1,
            "annotations": 1,
            "glossary_entries": 1,
            "conversations": 1,
            "messages": 1,
        }

    async def test_documents_dump_excludes_embedding_and_has_iso_datetime(
        self, backup_db, tmp_path
    ):
        staging = backup.prepare_staging(tmp_path / "stg")
        await backup.export_db_dumps(staging)

        documents = json.loads((staging / "db" / "documents.json").read_text(encoding="utf-8"))
        assert len(documents) == 2
        for doc in documents:
            assert "embedding" not in doc
            # 欄位齊全（白名單欄位）
            for key in (
                "id",
                "user_id",
                "project_id",
                "title",
                "filename",
                "file_path",
                "page_count",
                "status",
                "error_msg",
                "digest",
                "token_usage",
                "created_at",
            ):
                assert key in doc
            assert isinstance(doc["created_at"], str)

    async def test_annotations_and_glossary_anchor_fields_present(self, backup_db, tmp_path):
        staging = backup.prepare_staging(tmp_path / "stg")
        await backup.export_db_dumps(staging)

        annotations = json.loads((staging / "db" / "annotations.json").read_text(encoding="utf-8"))
        assert len(annotations) == 1
        ann = annotations[0]
        assert ann["document_id"] == 1
        assert _maybe_json(ann["bbox_list"]) == [[0, 0, 10, 10]]
        assert isinstance(ann["created_at"], str)
        assert isinstance(ann["updated_at"], str)

        glossary = json.loads(
            (staging / "db" / "glossary_entries.json").read_text(encoding="utf-8")
        )
        assert len(glossary) == 1
        assert glossary[0]["term"] == "term"

    async def test_conversations_and_messages_dumped(self, backup_db, tmp_path):
        staging = backup.prepare_staging(tmp_path / "stg")
        await backup.export_db_dumps(staging)

        conversations = json.loads(
            (staging / "db" / "conversations.json").read_text(encoding="utf-8")
        )
        messages = json.loads((staging / "db" / "messages.json").read_text(encoding="utf-8"))
        assert len(conversations) == 1
        assert len(messages) == 1
        assert messages[0]["content"] == "hello"
        assert messages[0]["conversation_id"] == conversations[0]["id"]

    async def test_settings_json_excludes_secret_keys_and_values(self, backup_db, tmp_path):
        staging = backup.prepare_staging(tmp_path / "stg")
        await backup.export_db_dumps(staging)

        settings_path = staging / "db" / "settings.json"
        raw_text = settings_path.read_text(encoding="utf-8")
        data = json.loads(raw_text)

        for secret_key in settings_store.SECRET_KEYS:
            assert secret_key not in raw_text
            assert secret_key not in data
        assert "nvapi-SUPER-SECRET-VALUE" not in raw_text
        assert "sk-ant-oat-SECRET-TOKEN" not in raw_text

        # 非秘密鍵照常保留
        assert data["llm_chat_model"] == "deepseek-ai/deepseek-v4-flash"
        assert data["translation_target_lang"] == "English"

    async def test_no_dump_file_contains_embedding_text(self, backup_db, tmp_path):
        staging = backup.prepare_staging(tmp_path / "stg")
        await backup.export_db_dumps(staging)

        for path in (staging / "db").glob("*.json"):
            assert "embedding" not in path.read_text(encoding="utf-8")


@pytest.mark.asyncio
class TestBuildManifest:
    async def test_manifest_structure_and_counts(self, backup_db, tmp_path, caplog):
        staging = backup.prepare_staging(tmp_path / "stg")
        counts = await backup.export_db_dumps(staging)

        # 讓第二篇文獻的 PDF 真實存在，供 manifest pdfs 清單命中。
        real_pdf = tmp_path / "second.pdf"
        real_pdf.write_bytes(b"%PDF-1.4 fake content")
        session_maker = backup_db["session_maker"]
        async with session_maker() as session:
            await session.execute(
                text("UPDATE documents SET file_path = :fp WHERE id = :id"),
                {"fp": str(real_pdf), "id": backup_db["second_doc_id"]},
            )
            await session.commit()

        with caplog.at_level("WARNING"):
            manifest = await backup.build_manifest(counts)

        assert manifest["format_version"] == 1
        assert manifest["app_version"] == backup.APP_VERSION
        settings = get_settings()
        assert manifest["embed_model"] == settings.embed_model
        assert manifest["embed_dim"] == settings.embed_dim
        # created_at 是可解析的 isoformat 字串
        from datetime import datetime

        datetime.fromisoformat(manifest["created_at"])

        expected_counts = dict(counts)
        expected_counts["pdfs"] = 1  # 只有 second.pdf 存在；doc id=1 的 /tmp/test.pdf 遺失
        assert manifest["counts"] == expected_counts

        assert manifest["pdfs"] == [
            {
                "name": "second.pdf",
                "document_id": backup_db["second_doc_id"],
                "size": real_pdf.stat().st_size,
            }
        ]
        # 遺失檔案應記 log warning，而非拋錯中止備份
        assert any("pdf missing" in r.getMessage() for r in caplog.records)

    async def test_no_pdfs_when_all_files_missing(self, backup_db, tmp_path):
        staging = backup.prepare_staging(tmp_path / "stg")
        counts = await backup.export_db_dumps(staging)

        manifest = await backup.build_manifest(counts)
        # 兩篇文獻都指向不存在的路徑（second_doc_id 尚未被改成真實檔案）
        assert manifest["pdfs"] == []
        assert manifest["counts"]["pdfs"] == 0


class TestPrepareCleanupStaging:
    def test_prepare_creates_db_subdir(self, tmp_path):
        staging = backup.prepare_staging(tmp_path / "stg")
        assert (staging / "db").is_dir()

    def test_prepare_wipes_stale_contents(self, tmp_path):
        base = tmp_path / "stg"
        staging = backup.prepare_staging(base)
        stale = staging / "db" / "documents.json"
        stale.write_text("stale", encoding="utf-8")

        staging_again = backup.prepare_staging(base)
        assert not stale.exists()
        assert (staging_again / "db").is_dir()

    def test_cleanup_removes_directory(self, tmp_path):
        staging = backup.prepare_staging(tmp_path / "stg")
        backup.cleanup_staging(staging)
        assert not staging.exists()

    def test_cleanup_missing_directory_is_noop(self, tmp_path):
        # 不存在也不應拋錯（上傳失敗提早 return 的情境）
        backup.cleanup_staging(tmp_path / "never-created")

    def test_default_staging_root_is_next_to_upload_dir(self):
        settings = get_settings()
        expected = Path(settings.upload_dir).parent / "backup_staging"
        assert backup._default_staging_root() == expected


class TestDumpTableRowsWhitelist:
    """repo.dump_table_rows：白名單以外一律拒絕（尤其 chunks，含 embedding）。"""

    def test_normalize_dump_row_converts_datetime_to_isoformat(self):
        from datetime import datetime as dt

        row = {"id": 1, "created_at": dt(2026, 1, 2, 3, 4, 5), "bbox_list": [[0, 0, 1, 1]]}
        normalized = repo._normalize_dump_row(row)
        assert normalized["created_at"] == "2026-01-02T03:04:05"
        assert normalized["bbox_list"] == [[0, 0, 1, 1]]  # 非 datetime 原樣保留

    @pytest.mark.asyncio
    async def test_chunks_table_rejected(self, backup_db):
        session_maker = backup_db["session_maker"]
        async with session_maker() as session:
            with pytest.raises(ValueError, match="not allowed"):
                await repo.dump_table_rows(session, "chunks")

    @pytest.mark.asyncio
    async def test_unknown_table_rejected(self, backup_db):
        session_maker = backup_db["session_maker"]
        async with session_maker() as session:
            with pytest.raises(ValueError, match="not allowed"):
                await repo.dump_table_rows(session, "users")
