"""真 Postgres 整合測試層的基礎設施（M15 T-FD-02）。

為什麼存在：其餘測試全跑 SQLite，Postgres 專有語意（TIMESTAMPTZ / JSONB /
vector `<=>` / CHECK 約束 / window function / ON CONFLICT）零覆蓋——M13 曾因此漏過
一個 datetime bug。這裡提供最小但真實的 Postgres 測試底座：

- 連線目標由環境變數 `TEST_DATABASE_URL` 決定，預設
  `postgresql+asyncpg://paper:paper@localhost:5432/paper_reader_test`。
- **連不上 Postgres 時整組 skip**（不是 fail）——CI / 無 docker 環境不被此層擋下。
- 測試庫由 session 級 fixture 建立：連系統庫 `postgres` → `DROP/CREATE DATABASE` →
  **跑真 migration**（重用 `app.db.migrate.migrate` 的套用邏輯，非手刻 DDL）。
- 每個測試函式間以 `TRUNCATE ... RESTART IDENTITY CASCADE` 隔離，並補回預設使用者
  （migration 001 種的 default@local，id 回到 1，對齊 repo.DEFAULT_USER_ID）。

用法：`py -m pytest -m pg`（需 compose db 在跑）。所有 tests/pg 下的測試自動掛上
`pg` marker（見 `pytest_collection_modifyitems`）。
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.db.migrate as migrate_mod

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://paper:paper@localhost:5432/paper_reader_test",
)

# migration 001 之後所有資料表（不含 schema_migrations）；每測試函式前 TRUNCATE 清空。
# settings 表也在其中：備份匯出讀 settings，但本層測試以 settings_store 記憶體 cache 為主，
# 清空無妨。
_DATA_TABLES = (
    "messages",
    "conversations",
    "annotations",
    "glossary_entries",
    "chunks",
    "documents",
    "projects",
    "settings",
    "users",
)


def _asyncpg_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


def _admin_dsn(url: str) -> tuple[str, str]:
    """由測試庫 URL 推出（系統庫 postgres 的 DSN, 測試庫名）。"""
    parsed = urlparse(_asyncpg_dsn(url))
    db_name = parsed.path.lstrip("/")
    admin = (
        f"postgresql://{parsed.username}:{parsed.password}"
        f"@{parsed.hostname}:{parsed.port or 5432}/postgres"
    )
    return admin, db_name


async def _create_test_database() -> None:
    import asyncpg

    admin_dsn, db_name = _admin_dsn(TEST_DATABASE_URL)
    try:
        conn = await asyncpg.connect(admin_dsn)
    except (OSError, asyncpg.PostgresError) as exc:  # pragma: no cover - 環境相依
        pytest.skip(f"Postgres 無法連線（{admin_dsn}）：{exc}；跳過 pg 整合測試層。")
    try:
        # 每個 session 重建乾淨的測試庫（FORCE 踢掉殘留連線；pg13+）。
        await conn.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        await conn.close()


async def _run_migrations() -> None:
    """重用 app.db.migrate 的套用邏輯，暫時把其 settings 指向測試庫。"""
    original = migrate_mod.get_settings
    migrate_mod.get_settings = lambda: SimpleNamespace(database_url=TEST_DATABASE_URL)
    try:
        await migrate_mod.migrate()
    finally:
        migrate_mod.get_settings = original


@pytest.fixture(scope="session")
def pg_database() -> str:
    """Session 級：建測試庫 + 跑真 migration。連不上則整組 skip。回傳測試庫 URL。

    同步 fixture 內以 `asyncio.run` 自建/收掉臨時 loop，避免與 pytest-asyncio 的
    function 級 loop 打架（session 級 async fixture 在 auto 模式下易踩 loop 綁定問題）。
    """
    asyncio.run(_create_test_database())
    asyncio.run(_run_migrations())
    return TEST_DATABASE_URL


@pytest.fixture
async def pg_db(pg_database: str):
    """Function 級：每測試前 TRUNCATE 隔離 + 補回預設使用者，回傳 (session_maker, engine)。

    用 NullPool 讓每個測試函式拿到不跨 loop 共用的連線，函式結束即 dispose。
    """
    engine = create_async_engine(pg_database, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {', '.join(_DATA_TABLES)} RESTART IDENTITY CASCADE"))
        # migration 001 種的預設使用者（id=1）被 TRUNCATE 清掉了，補回。
        await conn.execute(text("INSERT INTO users (email) VALUES ('default@local')"))
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield session_maker, engine
    finally:
        await engine.dispose()


def pytest_collection_modifyitems(config, items):
    """tests/pg 下的測試一律自動掛 `pg` marker（毋須每檔手動標）。"""
    for item in items:
        if "tests/pg/" in str(item.fspath).replace("\\", "/"):
            item.add_marker(pytest.mark.pg)
