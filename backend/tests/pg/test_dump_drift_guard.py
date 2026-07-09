"""備份欄位漂移守護（M15 T-FD-02，pg marker）。

背景（高嚴重度）：`repo._DUMP_TABLE_COLUMNS` 是備份匯出的欄位白名單，但它與實際
Postgres schema 之間沒有任何守護。未來某張 migration 幫白名單表加一個欄位，備份會**靜默
漏備份**該欄位，還原永遠救不回，且沒有測試會紅。

本測試以真 Postgres 的 `information_schema.columns` 為地面真相，對每張白名單表比對：

    實際欄位集合 == 白名單欄位 ∪ 顯式忽略欄位（EXPLICIT_EXCLUDED）

出現「既不在白名單、也不在忽略清單」的欄位 → **fail**，逼迫開發者顯式決定：
要嘛把新欄位加進 `_DUMP_TABLE_COLUMNS`（納入備份），要嘛加進 `EXPLICIT_EXCLUDED`
（明確不備份）。反向（白名單列了實際不存在的欄位）也 fail，抓 typo / 已刪欄位。

註：`chunks` 整表排除是設計（含 embedding，可由 PDF 重建，見 D10），不在白名單也不在
本守護範圍內。
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.db.repo import _DUMP_TABLE_COLUMNS

# 顯式忽略清單：實際存在於 schema、但**刻意不納入備份**的欄位。
# 目前六張白名單表的每個欄位都已納入備份，故各表忽略集合皆為空——這正是「顯式決定」的
# 起點：日後若新增一個不想備份的欄位，必須在此明確列出，否則本守護會 fail。
EXPLICIT_EXCLUDED: dict[str, set[str]] = {
    "documents": set(),
    "projects": set(),
    "annotations": set(),
    "glossary_entries": set(),
    "conversations": set(),
    "messages": set(),
}


async def _actual_columns(session, table: str) -> set[str]:
    rows = await session.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = :t
            """
        ),
        {"t": table},
    )
    return {r[0] for r in rows}


@pytest.mark.asyncio
async def test_dump_whitelist_covers_every_real_column(pg_db):
    """每張白名單表：實際欄位 == 白名單 ∪ 顯式忽略。任何缺口都 fail。"""
    session_maker, _ = pg_db
    problems: list[str] = []

    async with session_maker() as session:
        for table, whitelist_cols in _DUMP_TABLE_COLUMNS.items():
            actual = await _actual_columns(session, table)
            assert actual, f"表 {table} 不存在或無欄位（migration 是否漏跑？）"

            whitelist = set(whitelist_cols)
            excluded = EXPLICIT_EXCLUDED.get(table, set())

            # (1) 新欄位：實際有、但白名單與忽略清單都沒提到 → 必須顯式決定
            undecided = actual - whitelist - excluded
            if undecided:
                problems.append(
                    f"[{table}] 新欄位 {sorted(undecided)} 未被決定：請加進 "
                    f"repo._DUMP_TABLE_COLUMNS（納入備份）或本檔 EXPLICIT_EXCLUDED（明確不備份）。"
                )

            # (2) 白名單列了實際不存在的欄位（typo / 欄位被 migration 移除）
            phantom = whitelist - actual
            if phantom:
                problems.append(f"[{table}] 白名單欄位 {sorted(phantom)} 在實際 schema 不存在。")

            # (3) 忽略清單列了實際不存在的欄位（陳舊條目）
            stale_excluded = excluded - actual
            if stale_excluded:
                problems.append(
                    f"[{table}] EXPLICIT_EXCLUDED 的 {sorted(stale_excluded)} 已不在 schema。"
                )

    assert not problems, "備份欄位漂移偵測到問題：\n" + "\n".join(problems)


@pytest.mark.asyncio
async def test_chunks_not_in_whitelist(pg_db):
    """設計守恆：chunks 整表不在備份白名單（含 embedding，可由 PDF 重建）。"""
    assert "chunks" not in _DUMP_TABLE_COLUMNS
