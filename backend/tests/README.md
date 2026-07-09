# 後端測試說明

## 兩種模式

| 指令 | 跑什麼 | 需要 |
| --- | --- | --- |
| `py -m pytest -q` | 全套（SQLite 單元/整合 + pg 層）；**連不上 Postgres 時 pg 層自動 skip** | 無外部相依即可（pg 缺席只是 skip，不 fail） |
| `py -m pytest -m pg -q` | 只跑真 Postgres 整合測試層（`tests/pg/`） | compose db 在跑（`docker compose up -d db`，localhost:5432 可連） |
| `py -m pytest -m "not pg" -q` | 只跑 SQLite 快速層（明確排除 pg） | 無 |

> 主機用 `py -m pytest`（見 memory：主機無 node、pytest 走 `py -m`）。

## SQLite 層（預設、快）

`conftest.py` 的 `test_db` fixture 建記憶體 SQLite（M0–M11 核心表）。備份/還原測試另需
`conversations` / `messages` 兩表，統一由 `conftest.py` 的 `conversations_messages_tables`
fixture 建立（**單一定義處**——過去 test_backup / test_backup_export / test_restore 各自
手刻一份平行 DDL 副本，已收斂）。SQLite 版刻意精簡，不含 Postgres 專有語意。

## Postgres 層（`tests/pg/`，pg marker）

真 Postgres 才驗得到的語意：`TIMESTAMPTZ`、`JSONB` 運算子、pgvector `<=>` + window
function 防洗版、`CHECK` 約束、dump→restore 往返。

- 連線目標：環境變數 `TEST_DATABASE_URL`，預設
  `postgresql+asyncpg://paper:paper@localhost:5432/paper_reader_test`。
- `tests/pg/conftest.py` 的 session fixture：連系統庫 `postgres` → `DROP/CREATE DATABASE`
  測試庫 → **跑真 migration**（重用 `app.db.migrate`，非手刻 DDL）。連不上即整組 skip。
- 每測試函式間以 `TRUNCATE ... RESTART IDENTITY CASCADE` 隔離。
- `tests/pg/` 下所有測試自動掛 `pg` marker。

### 漂移守護（`test_dump_drift_guard.py`）

以 `information_schema.columns` 為地面真相，比對每張 `repo._DUMP_TABLE_COLUMNS` 白名單
表的實際欄位 == 白名單 ∪ `EXPLICIT_EXCLUDED`。未來 migration 幫白名單表加欄位卻沒更新
白名單/忽略清單 → 這條測試 fail，逼開發者顯式決定「備份或忽略」，堵住「靜默漏備份、還原
救不回」的破口。

### 準備 Postgres

```bash
docker compose up -d db      # 只起 db 服務即可（不需 api/web）
py -m pytest -m pg -q
```

測試庫（`paper_reader_test`）與開發庫（`paper_reader`）分離，pg 層每個 session 會重建它，
不會污染開發資料。
