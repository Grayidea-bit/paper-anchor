"""測試 fixture 與配置。"""

import json
from collections.abc import AsyncGenerator
from contextlib import ExitStack
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.main import app

# 使用記憶體 SQLite 用於測試
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="function")
async def test_db() -> AsyncGenerator[tuple[AsyncSession, create_async_engine], None]:
    """建立測試 DB 引擎與 session factory。"""
    engine = create_async_engine(
        TEST_DATABASE_URL,
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # 建立表格
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA foreign_keys = ON"))
        # 建立 users 表
        await conn.execute(
            text(
                """
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL UNIQUE,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        # 建立 projects 表
        await conn.execute(
            text(
                """
                CREATE TABLE projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        # 建立 documents 表
        await conn.execute(
            text(
                """
                CREATE TABLE documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
                    title TEXT NOT NULL DEFAULT '',
                    filename TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    page_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'uploaded'
                        CHECK (
                            status IN (
                                'uploaded', 'parsing', 'embedding',
                                'digesting', 'ready', 'failed'
                            )
                        ),
                    error_msg TEXT,
                    digest JSON,
                    token_usage JSON NOT NULL DEFAULT '{}',
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        # 建立 chunks 表
        await conn.execute(
            text(
                """
                CREATE TABLE chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    chunk_index INTEGER NOT NULL,
                    page INTEGER NOT NULL,
                    section TEXT,
                    content TEXT NOT NULL,
                    bbox_list JSON NOT NULL DEFAULT '[]',
                    embedding TEXT,
                    UNIQUE (document_id, chunk_index)
                )
                """
            )
        )
        # 建立 annotations 表
        await conn.execute(
            text(
                """
                CREATE TABLE annotations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    type TEXT NOT NULL CHECK (type IN ('underline', 'highlight', 'note')),
                    color TEXT NOT NULL DEFAULT 'amber'
                        CHECK (color IN ('amber', 'terracotta', 'sage', 'slate')),
                    page INTEGER NOT NULL,
                    bbox_list JSON NOT NULL DEFAULT '[]',
                    chunk_id INTEGER REFERENCES chunks(id) ON DELETE SET NULL,
                    selected_text TEXT NOT NULL DEFAULT '',
                    note_text TEXT NOT NULL DEFAULT '',
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        # 建立 glossary_entries 表
        await conn.execute(
            text(
                """
                CREATE TABLE glossary_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    term TEXT NOT NULL,
                    translation TEXT NOT NULL DEFAULT '',
                    target_lang TEXT NOT NULL,
                    page INTEGER NOT NULL,
                    bbox_list JSON NOT NULL DEFAULT '[]',
                    chunk_id INTEGER REFERENCES chunks(id) ON DELETE SET NULL,
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        # 插入預設使用者
        await conn.execute(text("INSERT INTO users (email) VALUES ('default@local')"))
        # 插入測試文獻
        await conn.execute(
            text(
                """
                INSERT INTO documents
                    (user_id, title, filename, file_path, page_count, status)
                VALUES (1, 'Test Document', 'test.pdf', '/tmp/test.pdf', 2, 'ready')
                """
            )
        )
        # 插入測試 chunk
        await conn.execute(
            text(
                """
                INSERT INTO chunks (document_id, chunk_index, page, section, content, bbox_list)
                VALUES (1, 0, 1, 'intro', 'Test content', '[[0, 0, 100, 50]]')
                """
            )
        )
        await conn.commit()

    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield session_maker, engine
    await engine.dispose()


# conversations / messages 的 SQLite 測試 DDL（單一定義處）。
# 主 test_db fixture 只建 M0–M11 核心表；備份/還原相關測試（test_backup*/test_restore）
# 額外需要這兩張表，過去各檔手刻一份平行副本 → 收斂於此，避免欄位漂移。
# 註：真 Postgres schema（migrations 001/002/004）另有 conversations 的 scope CHECK
# 約束等語意，此處 SQLite 版刻意精簡，Postgres 專有語意由 tests/pg 覆蓋。
_CONVERSATIONS_SQLITE_DDL = """
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

_MESSAGES_SQLITE_DDL = """
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


@pytest.fixture(scope="function")
async def conversations_messages_tables(
    test_db: tuple[async_sessionmaker, create_async_engine],
) -> tuple[async_sessionmaker, create_async_engine]:
    """在 SQLite test_db 之上補建 conversations / messages 兩張表，回傳同樣的
    (session_maker, engine) tuple。備份/還原測試以此取代各自手刻的 CREATE TABLE。
    """
    session_maker, engine = test_db
    async with engine.begin() as conn:
        await conn.execute(text(_CONVERSATIONS_SQLITE_DDL))
        await conn.execute(text(_MESSAGES_SQLITE_DDL))
    return session_maker, engine


# 各模組以 `from app.db.session import SessionLocal` 匯入，綁定各自模組層級名稱；
# 只 patch `app.db.session.SessionLocal` 不會反映到已匯入的名稱，需逐一 patch。
_SESSION_LOCAL_MODULES = [
    "app.db.session",
    "app.settings_store",
    "app.routers.annotations",
    "app.routers.conversations",
    "app.routers.documents",
    "app.routers.glossary",
    "app.routers.projects",
    "app.services.digest",
    "app.services.ingest",
    "app.services.reembed",
    "app.tools.keyword_search",
    "app.tools.list_annotations",
]


@pytest.fixture(scope="function")
async def async_client(
    test_db: tuple[async_sessionmaker, create_async_engine],
) -> AsyncGenerator[AsyncClient, None]:
    """提供測試用的 HTTP 客戶端，使用測試 DB。"""
    session_maker, _ = test_db

    # 覆蓋 SessionLocal 以使用測試 DB（見上方模組清單註解）
    with ExitStack() as stack:
        for module_path in _SESSION_LOCAL_MODULES:
            stack.enter_context(patch(f"{module_path}.SessionLocal", session_maker))
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest.fixture(scope="function")
async def setup_test_document(
    test_db: tuple[async_sessionmaker, create_async_engine],
) -> tuple[int, int]:
    """
    建立測試文獻與一個 chunk，返回 (doc_id, chunk_id)。
    用於標註測試。
    """
    session_maker, _ = test_db
    async with session_maker() as session:
        # 建立文獻
        result = await session.execute(
            text(
                """
                INSERT INTO documents (user_id, title, filename, file_path, page_count, status)
                VALUES (1, 'Test Document', 'test.pdf', '/tmp/test.pdf', 2, 'ready')
                RETURNING id
                """
            )
        )
        doc_id = result.scalar()
        # 建立 chunk
        result = await session.execute(
            text(
                """
                INSERT INTO chunks (document_id, chunk_index, page, section, content, bbox_list)
                VALUES (:doc_id, 0, 1, 'intro', 'Test content', :bbox_list)
                RETURNING id
                """
            ),
            {"doc_id": doc_id, "bbox_list": json.dumps([[0, 0, 100, 50]])},
        )
        chunk_id = result.scalar()
        await session.commit()
    return doc_id, chunk_id


@pytest.fixture(scope="function")
async def seeded_chunks(
    test_db: tuple[async_sessionmaker, create_async_engine],
) -> dict:
    """
    建立兩份文獻、各數個 chunks，page/bbox_list/chunk_index 互異。
    供 repo 層 chunks_by_ids / chunks_by_indexes 測試使用（T-AN-08）。

    回傳 {"doc_a": id, "doc_b": id, "chunks_a": [dict,...], "chunks_b": [dict,...]}
    每個 chunk dict 含 id/chunk_index/page/bbox_list/content。
    """
    session_maker, _ = test_db
    async with session_maker() as session:
        doc_a = (
            await session.execute(
                text(
                    """
                    INSERT INTO documents
                        (user_id, title, filename, file_path, page_count, status)
                    VALUES (1, 'Paper A', 'a.pdf', '/tmp/a.pdf', 10, 'ready')
                    RETURNING id
                    """
                )
            )
        ).scalar()
        doc_b = (
            await session.execute(
                text(
                    """
                    INSERT INTO documents
                        (user_id, title, filename, file_path, page_count, status)
                    VALUES (1, 'Paper B', 'b.pdf', '/tmp/b.pdf', 10, 'ready')
                    RETURNING id
                    """
                )
            )
        ).scalar()

        async def _insert_chunks(doc_id: int, specs: list[tuple[int, int, list]]) -> list[dict]:
            created = []
            for chunk_index, page, bbox in specs:
                result = await session.execute(
                    text(
                        """
                        INSERT INTO chunks
                            (document_id, chunk_index, page, section, content, bbox_list)
                        VALUES (:doc_id, :chunk_index, :page, 'body', :content, :bbox_list)
                        RETURNING id
                        """
                    ),
                    {
                        "doc_id": doc_id,
                        "chunk_index": chunk_index,
                        "page": page,
                        "content": f"doc{doc_id} chunk{chunk_index} content",
                        "bbox_list": json.dumps(bbox),
                    },
                )
                cid = result.scalar()
                created.append(
                    {
                        "id": cid,
                        "chunk_index": chunk_index,
                        "page": page,
                        "bbox_list": bbox,
                    }
                )
            return created

        chunks_a = await _insert_chunks(
            doc_a,
            [
                (0, 1, [[0, 0, 10, 10]]),
                (1, 1, [[0, 10, 10, 20]]),
                (2, 2, [[0, 20, 10, 30]]),
            ],
        )
        chunks_b = await _insert_chunks(
            doc_b,
            [
                (0, 5, [[100, 0, 110, 10]]),
                (1, 6, [[100, 10, 110, 20]]),
            ],
        )
        await session.commit()
    return {"doc_a": doc_a, "doc_b": doc_b, "chunks_a": chunks_a, "chunks_b": chunks_b}
