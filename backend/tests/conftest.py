"""測試 fixture 與配置。"""

import json
from collections.abc import AsyncGenerator
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


@pytest.fixture(scope="function")
async def async_client(
    test_db: tuple[async_sessionmaker, create_async_engine],
) -> AsyncGenerator[AsyncClient, None]:
    """提供測試用的 HTTP 客戶端，使用測試 DB。"""
    session_maker, _ = test_db

    # 覆蓋 SessionLocal 以使用測試 DB
    with patch("app.db.session.SessionLocal", session_maker):
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
