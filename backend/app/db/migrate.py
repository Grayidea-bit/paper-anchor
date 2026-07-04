"""Apply SQL migrations in order. Usage: python -m app.db.migrate

Files in app/db/migrations/*.sql are applied by filename sort order,
tracked in schema_migrations. Each file runs in a single transaction.
"""

import asyncio
import sys
from pathlib import Path

import asyncpg

from app.config import get_settings

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def _asyncpg_dsn() -> str:
    return get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://")


async def migrate() -> None:
    conn = await asyncpg.connect(_asyncpg_dsn())
    try:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                name TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        applied = {r["name"] for r in await conn.fetch("SELECT name FROM schema_migrations")}
        pending = [f for f in sorted(MIGRATIONS_DIR.glob("*.sql")) if f.name not in applied]
        if not pending:
            print("migrate: up to date")
            return
        for f in pending:
            async with conn.transaction():
                await conn.execute(f.read_text(encoding="utf-8"))
                await conn.execute("INSERT INTO schema_migrations (name) VALUES ($1)", f.name)
            print(f"migrate: applied {f.name}")
    finally:
        await conn.close()


async def _wait_for_db(retries: int = 30, delay: float = 1.0) -> None:
    for attempt in range(retries):
        try:
            conn = await asyncpg.connect(_asyncpg_dsn())
            await conn.close()
            return
        except (OSError, asyncpg.PostgresError):
            if attempt == retries - 1:
                raise
            await asyncio.sleep(delay)


if __name__ == "__main__":
    try:
        asyncio.run(_wait_for_db())
        asyncio.run(migrate())
    except Exception as e:  # noqa: BLE001 - CLI entry point
        print(f"migrate: FAILED: {e}", file=sys.stderr)
        sys.exit(1)
