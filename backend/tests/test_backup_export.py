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
    async def test_manifest_structure_and_counts(self, backup_db, tmp_path, caplog, monkeypatch):
        staging = backup.prepare_staging(tmp_path / "stg")
        counts = await backup.export_db_dumps(staging)

        # v2：embed_model/embed_dim 取 effective_embed_config()（反映實際生效來源而非 .env）；
        # monkeypatch 成固定值以斷言「取自 effective 而非 env」。
        monkeypatch.setattr(
            backup, "effective_embed_config", lambda: ("local", "BAAI/bge-m3", 1024)
        )

        # 讓第二篇文獻的 PDF 真實存在，供 manifest pdfs 清單命中。
        real_pdf = tmp_path / "second.pdf"
        real_pdf.write_bytes(b"%PDF-1.4 fake content")
        session_maker = backup_db["session_maker"]
        second_uuid = "second"  # file_path stem → chunk 檔名/映射鍵
        async with session_maker() as session:
            await session.execute(
                text("UPDATE documents SET file_path = :fp WHERE id = :id"),
                {"fp": str(real_pdf), "id": backup_db["second_doc_id"]},
            )
            await session.commit()

        # chunk_counts 模擬 export_chunk_dumps 的回傳（second.pdf → 3 chunks）。
        chunk_counts = {second_uuid: 3}
        with caplog.at_level("WARNING"):
            manifest = await backup.build_manifest(counts, chunk_counts)

        assert manifest["format_version"] == 2
        assert manifest["app_version"] == backup.APP_VERSION
        assert manifest["embed_model"] == "BAAI/bge-m3"  # 取自 effective，非 env 的 NIM 模型
        assert manifest["embed_dim"] == 1024
        settings = get_settings()
        assert manifest["embed_model"] != settings.embed_model  # 明確有別於 .env
        # created_at 是可解析的 isoformat 字串
        from datetime import datetime

        datetime.fromisoformat(manifest["created_at"])

        expected_counts = dict(counts)
        expected_counts["chunks"] = 3  # v2 新增 chunks 總數
        expected_counts["pdfs"] = 1  # 只有 second.pdf 存在；doc id=1 的 /tmp/test.pdf 遺失
        assert manifest["counts"] == expected_counts

        assert manifest["pdfs"] == [
            {
                "name": "second.pdf",
                "document_id": backup_db["second_doc_id"],
                "size": real_pdf.stat().st_size,
            }
        ]
        # v2 新增 chunk_files：每篇一個 chunks/{uuid}.json 的清單，帶 document_id。
        assert manifest["chunk_files"] == [
            {"name": "second.json", "document_id": backup_db["second_doc_id"], "chunks": 3}
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
        # 無 chunk_counts → chunks 總數 0、chunk_files 空（v1-style 呼叫仍相容）
        assert manifest["counts"]["chunks"] == 0
        assert manifest["chunk_files"] == []


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


class TestVectorCodec:
    """float32 LE ⇄ base64 codec（M14 D12 C）：roundtrip 精度 + 防禦分支。"""

    def test_roundtrip_1024_dim_precision(self):
        import random

        rng = random.Random(42)
        vec = [rng.uniform(-1.0, 1.0) for _ in range(1024)]  # 真尺寸
        restored = backup.b64_to_vector(backup.vector_to_b64(vec))
        assert len(restored) == 1024
        assert max(abs(a - b) for a, b in zip(vec, restored, strict=True)) < 1e-6

    def test_accepts_pgvector_text_string(self):
        # dump_chunks 經 embedding::text 讀回的字面字串（恰為合法 JSON 陣列）。
        restored = backup.b64_to_vector(backup.vector_to_b64("[0.5, -0.25, 0.125]"))
        assert restored == [0.5, -0.25, 0.125]  # 皆為 float32 可精確表示的值

    def test_list_and_string_inputs_agree(self):
        assert backup.vector_to_b64([0.5, -0.25, 0.125]) == backup.vector_to_b64(
            "[0.5, -0.25, 0.125]"
        )

    def test_invalid_base64_raises_valueerror(self):
        with pytest.raises(ValueError):
            backup.b64_to_vector("!!!not-valid-base64!!!")

    def test_byte_length_not_multiple_of_four_raises(self):
        import base64

        bad = base64.b64encode(b"abcde").decode("ascii")  # 5 bytes → 非 4 的倍數
        with pytest.raises(ValueError, match="非 4 的倍數"):
            backup.b64_to_vector(bad)


@pytest.mark.asyncio
class TestExportChunkDumps:
    """export_chunk_dumps 結構（M14 D12 C）。dump_chunks 為 Postgres 專用（embedding::text
    SQLite 不通），故此處 monkeypatch dump_chunks 餵假列，只驗 export 編排與檔案結構；
    dump_chunks 本體（真向量 roundtrip）在 tests/pg 覆蓋。
    """

    async def test_writes_chunk_file_per_document_with_b64_and_null(
        self, backup_db, tmp_path, monkeypatch
    ):
        staging = backup.prepare_staging(tmp_path / "stg")

        monkeypatch.setattr(
            backup, "effective_embed_config", lambda: ("local", "BAAI/bge-m3", 1024)
        )

        # 假 dump_chunks：doc id=1（conftest，file_path=/tmp/test.pdf）回兩塊——一塊有向量
        # （pgvector text 字面字串），一塊 embedding=None（尚未嵌入照出）；其餘文獻回 []。
        async def _fake_dump_chunks(session, doc_id):
            if doc_id == 1:
                return [
                    {
                        "id": 10,
                        "chunk_index": 0,
                        "page": 1,
                        "section": "intro",
                        "content": "hello",
                        "bbox_list": [[0, 0, 10, 10]],
                        "embedding": "[0.5,-0.25,0.125]",
                    },
                    {
                        "id": 11,
                        "chunk_index": 1,
                        "page": 2,
                        "section": None,
                        "content": "world",
                        "bbox_list": [[1, 1, 5, 5]],
                        "embedding": None,
                    },
                ]
            return []

        monkeypatch.setattr(repo, "dump_chunks", _fake_dump_chunks)

        chunk_counts = await backup.export_chunk_dumps(staging)

        # file_path=/tmp/test.pdf → stem "test" → chunks/test.json
        assert chunk_counts == {"test": 2}
        chunk_path = staging / "chunks" / "test.json"
        assert chunk_path.exists()
        data = json.loads(chunk_path.read_text(encoding="utf-8"))
        assert data["embed_model"] == "BAAI/bge-m3"
        assert data["embed_dim"] == 1024
        assert len(data["chunks"]) == 2

        first, second = data["chunks"]
        assert first["id"] == 10
        assert first["chunk_index"] == 0
        assert first["section"] == "intro"
        assert first["bbox_list"] == [[0, 0, 10, 10]]
        # embedding 為 base64 字串，解回等於原向量
        assert isinstance(first["embedding"], str)
        assert backup.b64_to_vector(first["embedding"]) == [0.5, -0.25, 0.125]
        # 未嵌入的 chunk embedding 欄為 null
        assert second["embedding"] is None

    async def test_documents_without_chunks_are_skipped(self, backup_db, tmp_path, monkeypatch):
        staging = backup.prepare_staging(tmp_path / "stg")
        monkeypatch.setattr(
            backup, "effective_embed_config", lambda: ("local", "BAAI/bge-m3", 1024)
        )

        async def _empty(session, doc_id):
            return []

        monkeypatch.setattr(repo, "dump_chunks", _empty)

        chunk_counts = await backup.export_chunk_dumps(staging)
        assert chunk_counts == {}
        # chunks 目錄仍建立（供 run_backup glob），但無檔案
        assert (staging / "chunks").is_dir()
        assert list((staging / "chunks").glob("*.json")) == []
