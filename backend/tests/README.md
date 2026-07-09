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

pg 層預設連 `localhost:5432`（`TEST_DATABASE_URL` 預設值）。但為縮小暴露面（M15 T-FD-04），
`docker-compose.yaml` 的 db 服務**預設不對主機公開 5432 埠**——api 走 Compose 內網連 db，
主機平時無需直連。因此跑 pg 測試層前，需先讓 localhost:5432 可連：

```bash
# 1. 取消註解 docker-compose.yaml 中 db 服務下的 ports 區塊（綁 127.0.0.1:5432）：
#      ports:
#        - "127.0.0.1:5432:5432"
# 2. 起 db（只需 db，不需 api/web）
docker compose up -d db
# 3. 跑 pg 層
py -m pytest -m pg -q
```

（不想改 compose 也可用其他方式讓 localhost:5432 通，例如另跑一個對外的 pg 容器，或設
`TEST_DATABASE_URL` 指向可連的實例。）跑完把 ports 區塊改回註解即可恢復預設不暴露狀態。

測試庫（`paper_reader_test`）與開發庫（`paper_reader`）分離，pg 層每個 session 會重建它，
不會污染開發資料。
