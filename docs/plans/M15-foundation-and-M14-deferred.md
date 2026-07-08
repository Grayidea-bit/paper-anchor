# M15 — 地基強化 / Foundation Hardening（本次執行 / to execute now）

> 使用者判斷：系統仍在開發期，M12/M13 的備份還原偏「蓋房子」，先回頭鞏固地基。三路 Opus 全系統審查（資料層地基／服務層／前端）產出本計畫。**M14 計畫保留於本檔下半部，暫緩執行。**
> User's call: the system is still in development; backup/restore was "building the house". Three parallel Opus reviews (data-layer foundation / services / frontend) produced this plan. **The M14 plan is retained in the lower half of this file, deferred.**

## Context 背景

審查總評：地基整體穩健（SQL 注入面乾淨、秘密遮罩完整、引用鏈完好、XSS 無可乘之機、備份原子性論證成立），但有三個高嚴重度缺陷與一批「隨庫成長會惡化／會咬人」的中等問題。
Overall verdict: the foundation is sound (no injection surface, secrets masked, citation chain intact, no XSS, backup atomicity holds), but three high-severity defects and a batch of grows-worse-over-time issues need fixing.

**三個高嚴重度 Three high-severity findings：**
1. **文獻黑洞 Ingest black hole**：ingest 中途程序被殺（重啟/OOM/`--reload`），文獻永久卡 `parsing`/`embedding`，無重試入口；restore 修復只認 `failed` 救不了它；`insert_chunks` 不冪等使半殘狀態無法重跑。
2. **備份 schema 漂移無守護 Backup column-drift unguarded**：`repo._DUMP_TABLE_COLUMNS` 白名單 vs 實際 schema 無測試比對——未來 migration 加欄位，備份靜默漏、還原永遠救不回；且核心 Postgres 語意（TIMESTAMPTZ/JSONB/vector/CHECK）零整合測試覆蓋，SQLite 替身已漏過一次 datetime bug（M13）。
3. **前端連接卡死 Connect button deadlock**：OAuth 進行中關閉設定視窗，`backupStore.loading` 永遠 true（module 級單例 + interval 被清），重開後「連接」永久 disabled。

## 任務卡 / Task Cards（含模型分工）

| 卡 | 模型 | 內容 | 依賴 |
|---|---|---|---|
| T-FD-00 | Opus | **文件先行**：§5 加 `POST /api/documents/{id}/reingest`；02-architecture 補「單 process 部署假設」與「預設信任網段」章節、001 migration 的 ANN 索引註記更新（library/project scope 為全表掃描，記門檻卡）；roadmap 開 M15 各卡 | — |
| T-FD-01 | Sonnet | **ingest 冪等與自癒**：`ingest_document` 開頭無條件 `delete_chunks`（廉價換冪等）；lifespan 啟動 reconciliation（`parsing/embedding` 殘態 → `failed` + error_msg）；新端點 `POST /api/documents/{id}/reingest`（409 若在跑）+ 前端 failed 文獻「重新解析」按鈕；restore 修復範圍擴及 transient 狀態。測試：崩潰殘態重置、重跑不撞 UNIQUE、reingest 端點 | 00 |
| T-FD-02 | Opus | **Postgres 整合測試層 + 漂移守護**：薄 Postgres 測試層（用 compose 的 db 或 testcontainers，跑真 migration，消滅 4 份手刻 DDL 副本）；覆蓋：`information_schema` vs `_DUMP_TABLE_COLUMNS` 欄位守護（新欄位必須顯式決定備份或忽略）、`similar_chunks_scoped`（vector `<=>`）、`total_token_usage`（JSONB）、backup dump→restore 往返、scope CHECK、TIMESTAMPTZ。標記為獨立 pytest marker（無 Postgres 時 skip） | 00 |
| T-FD-03 | Sonnet | **前端正確性批次**：connect loading 卡死（`stopPolling` 重置 loading）；mid-stream 錯誤後 retry 產生重複提問（剝除失敗組再重送）；SSE 壞 frame 殺整條流（`JSON.parse` 包 try/catch 略過）；backup/restore 間 error 殘留清除 | — |
| T-FD-04 | Opus | **安全批次**（安全敏感）：compose 埠綁定改 `127.0.0.1`（db 直接拿掉對外埠）+ db 強密碼；無 body 的 state-changing 端點強制 `application/json`（堵跨站 form POST 觸發 restore/disconnect）；lifespan 偵測多 worker 即警告（鎖與快取為 per-process）；README 部署假設註記 | 00 |
| T-FD-05 | Sonnet | **前端串流/記憶體效能**：訊息抽 `React.memo` 子元件（token 串流不再重算全列表 markdown）；捲動改「在底部附近才自動跟隨」+ 串流中 `auto` 行為；PDF canvas 離開可視範圍回收（保留佔位高度）+ 換文獻 `key={documentId}` 重建；scroll handler rAF 節流 | — |
| T-FD-06 | Sonnet | **後端寫入效能**：`insert_chunks` 改單條多列 INSERT、`update_chunk_embeddings` 改 executemany/VALUES JOIN（消 N+1 round-trip）；restore 的 Drive 下載移出 DB session（先下載後開 session，比照 backup 慣例）；migration 補 `annotations.chunk_id`/`glossary_entries.chunk_id` 兩個 FK 索引 | 02（用其測試層驗證） |
| T-FD-07 | Sonnet | **工具 schema 對齊**：`tools/_input_schema` 解析 docstring Args 段補齊 per-param description/required/預設值，消除 Claude 後端工具品質劣化；兩後端 schema 一致性測試 | — |
| T-FD-08 | Haiku | **記帳清理批次**：死 i18n 鍵刪除；aria-label 入 i18n；prompt 載入 assert 佔位符存在；`parse_citations` 正則放寬對齊文件（或改文件）；`similar_chunks_scoped` 單篇分支補 `status='ready'`；digest `_select_chunks` O(n²) 改 set；`APP_VERSION` 單一來源；`.env.example` 同步檢查 | 01–07 後 |
| T-FD-99 | Opus | **整合驗證**：pytest（SQLite + 新 Postgres 層）/ruff/npm build 全綠；瀏覽器實測（connect 卡死重現路徑修復、串流長回答捲動、failed 文獻重新解析、100 頁文獻記憶體觀測）；引用鏈回歸（動了 ingest/insert_chunks，跑 eval_citations 或以 Postgres 層等效覆蓋）；roadmap 勾選 | 全部 |

並行性 Parallelism：T-FD-01/03/05/07 互不重疊可與 T-FD-02 並行；T-FD-04 獨立；T-FD-06 等 02。
明確**不做**（審查建議但擱置）Deferred：HNSW/ivfflat 向量索引（門檻未到，記卡）；`repo.py` 拆檔（可讀性尚可）；usage 端點快取；Library `prompt()` 改 popover；Claude 後端歷史保真度（SDK 固有限制，註解記錄）。

## 驗證 / Verification

1. `py -m pytest`（backend 目錄，SQLite 全套不退化）+ 新 Postgres marker 測試層全過 + `ruff check` + `docker compose exec web npm run build`。
2. 黑洞重現測試：ingest 進行中 `docker compose restart api` → 啟動後文獻變 `failed` 可一鍵重新解析成功。
3. 漂移守護自證：暫時在 migration 加一個假欄位 → Postgres 守護測試必須 fail。
4. 前端：連接中關 modal → 重開 → 連接鈕可用；長回答串流中向上捲動不被拉回。
5. 安全：區網另一台裝置連 5432/8000 應失敗（或明確記錄僅本機）。
6. 引用鏈：eval_citations 不退化（或 Postgres 層的 ingest→檢索→citation 等效測試）。

---
---

# 【保留 RETAINED・暫緩 DEFERRED】M14 — 本地 Embedding + digest 分派 + 備份格式 v2 / Local Embedding + Digest Dispatch + Backup Format v2

> M12（備份）/ M13（還原 + 設定頁重構）已完成。本計畫解使用者指出的架構債：**Claude 訂閱與 NVIDIA NIM 目前不是擇一關係**。使用者決定先做地基強化（M15），本計畫保留待後續執行。
> M12 (backup) / M13 (restore + settings redesign) are done. This plan resolves the user-identified architectural debt: **Claude subscription and NVIDIA NIM are not currently an either-or choice.** Deferred in favor of foundation hardening (M15).

## Context 背景

即使對話切到 Claude 訂閱，仍有三處寫死依賴 NIM：ingest 的 chunk embedding、每次提問的 query embedding（rag 檢索）、digest 導讀生成（`digest.py` 直呼 `llm.chat`，繞過 chat_backend 分派）。Anthropic 無 embedding API，故補法是**內建本地 embedding 模型**。順帶採納使用者的第二個需求：**embedding 向量進雲端備份**（格式 v2），讓還原免重嵌免重解析、標註的 chunk 關聯不再丟失。
Even with chat on Claude subscription, three paths hard-depend on NIM: chunk embedding at ingest, query embedding per question, and digest generation (`digest.py` calls `llm.chat` directly, bypassing chat_backend dispatch). Anthropic has no embeddings API, so the fix is a **built-in local embedding model**. We also adopt the user's second request: **embeddings go into the cloud backup** (format v2) — restore becomes re-embed-free and re-parse-free, and annotation↔chunk links survive.

三個子項 Three workstreams：
- **A. 本地 embedding**：候選 BGE-M3 / snowflake-arctic-embed-l-v2.0（皆 1024 維，`VECTOR(1024)` 零 migration）、fastembed CPU int8、懶載入、模型檔 volume 快取；`embed_source` 設定（auto/nim/local，auto＝有 key 用 NIM）。
- **B. digest 走 chat_backend**：新增非串流 `agent.chat_once()`（消費 stream_chat 事件，天然繼承分派與重試），digest 改呼叫之。
- **C. 備份 v2**：chunks + 向量（base64 float32）進備份；還原三路分派（相符直灌／不符用 dump content 重嵌／v1 走舊路）。

## A. 本地 Embedding（關鍵決策 / key decisions）

- `embed_passages`/`embed_query` 維持 **llm.py 唯一入口**，內部依 `embed_source` 分派——兩個呼叫點（ingest.py:122、conversations.py:131）零改動，鐵律 1/3 不被觸碰。
  llm.py stays the single entry; both call sites untouched.
- 本地推論收束於新模組 `backend/app/local_embed.py`（僅 llm.py 得 import；CLAUDE.md 鐵律 3 補註）。懶載入模組級單例 + `asyncio.to_thread`（不阻塞事件迴圈、不拖慢啟動）。
- **只把 `embed_source` 執行期化**（settings_store + SettingsUpdate + 守護測試）；embed 端點/key/模型維持 `.env`——來源選擇是使用者層決策（同 chat_backend 先例），key 執行期化會讓「全庫向量何時失效」不可推理。
- 本地模型名寫死常數 `LOCAL_EMBED_MODEL`（**由 T-EM-00 煙霧測試拍板**：BGE-M3 int8 變體 vs arctic-l-v2.0；RAM/速度/中英檢索品質/wheel 體積實測）。回傳前 assert 維度==1024（壞模型炸在入庫前）。**本地路徑也要區分 passage/query**（arctic 需 "query: " 前綴——D5 同款陷阱）。
- 新增 `llm.effective_embed_config() -> (source, model, dim)` 單一真相：backup manifest、restore 相符判斷、healthz、前端顯示共用。
- **重建索引維護動作**：`POST /api/maintenance/reembed`（202/409）+ `services/reembed.py`——逐文獻 `get_chunks` → `embed_passages` → `update_chunk_embeddings`（內容在 DB，免重解析）；**沿用 backup 的 `try_begin` 鎖**（backup/restore/reembed 三方互斥，防混模型向量被 dump），進度走既有 status 的 `operation:"reembed"`。切換來源的警告為前端文案 + 重建按鈕（不做全庫模型追蹤——單機工具，成本大於收益）。
- 模型檔快取：config 加 `embed_cache_dir="/data/models"` + compose 新 `models:` volume（重建容器不重下載）。requirements 加 `fastembed`（版本由煙霧測試 pin）。

## B. digest 分派（關鍵決策）

- `agent.chat_once(system, user_content, *, max_tokens=3000, deps=None) -> (text, usage)`：消費 stream_chat 事件（累積 token、取 usage），零複製分派/重試邏輯。
- **停用工具**：`stream_chat`（agent + claude_backend 兩份）加 `with_tools: bool = True`，chat_once 傳 False——digest 純摘要不需工具，省 token 且決定性高；預設 True 既有行為零變。
- `max_tokens` 只在 openai 路徑生效（ModelSettings）；claude-sdk 忽略（docstring 註明）。
- digest.py 只改呼叫處；`extract_json`/`_validate` 一字不動（鐵律 1 守門）。ThinkFilter 已在 stream_chat 內，不雙重過濾。
- `glossary.py` 仍走 `llm.chat`，**本次不動**（鐵律 7 範圍紀律），記入發現事項留後續小卡。

## C. 備份格式 v2（關鍵決策）

- `FORMAT_VERSION = 2`；restore 檢查改 `not in (1, 2)`——**v1 舊備份繼續可還原**。
- chunks dump **每文獻一檔** `chunks/{pdf-uuid}.json`：`{embed_model, embed_dim, chunks:[{id, chunk_index, page, section, content, bbox_list, embedding}]}`，embedding 為 **float32 LE → base64**（比 JSON 浮點省 ~60%）。每次全量覆蓋（reembed/修復會改向量且無變更訊號，正確性優先）。
- 向量讀回：新 repo 函式 `dump_chunks(session, doc_id)` 用 `embedding::text`（與寫入端 `CAST(:emb AS vector)` 對稱）；**不放寬 dump_table_rows 白名單**。
- manifest v2：`embed_model`/`embed_dim` 改取 `effective_embed_config()`（反映實際來源而非 .env）、counts 加 chunks、新增 `chunk_files` 清單。
- **還原三路分派**（新文獻）：
  | 路徑 Path | 條件 Condition | 行為 Behavior |
  |---|---|---|
  | (a) 直灌 fast | v2 且 manifest 模型==現行生效 | insert_chunks + base64 解碼 update_chunk_embeddings——**零 LLM 零解析** |
  | (b) 重嵌 re-embed | v2 但模型不符 | insert_chunks（content 從 dump）+ embed_passages——免重解析 |
  | (c) 全重建 full | v1 或 chunk 檔缺失/損壞 | 現行下載 PDF 重 ingest 路徑（robustness fallback） |
- **chunk 插入提前到 merge phase**（annotations/glossary 的 chunk_id remap 需要新 chunk id）；向量填充留 ingest phase（進度沿用 `ingest` phase 名，前端零改動）。狀態機：插 doc 時 status='embedding'，向量填完 'ready'。
- **chunk_id remap**：dump 的 annotations/glossary 本就帶舊 chunk_id（dump 端零改動）；chunk 檔帶舊 id → `old_chunk_id → chunk_index → new_chunk_id` 映射；`restore_insert_annotation`/`restore_insert_glossary_entry` 加 optional `chunk_id`（預設 NULL＝v1 行為）。
- failed 文獻修復路徑同樣升級（相符直灌）；冪等收斂保證（D11）在 v2 成立且更快。

## 文件（鐵律 5，文件先行）

02-architecture：新 **D12** 節（M14 全文）、§1 選型表 Embedding 列、D5 補 passage/query 註記、D10 settings 鍵表加 `embed_source`、§5 加 reembed 端點與 status operation 值域；roadmap 開 M14；CLAUDE.md 鐵律 3 補 local_embed 註記；.env.example 加 EMBED_CACHE_DIR。

## 任務卡與模型分工 / Task Cards & Model Assignment

| 卡 Card | 模型 | 內容 | 依賴 |
|---|---|---|---|
| T-EM-00 | Opus | **煙霧測試先行**（M7/M8 慣例）：容器內實測候選模型——fastembed 支援與 query prefix 核實、RAM 峰值/速度/維度、中英檢索品質 vs NIM spot check、wheel 體積；產出決策記錄（模型/變體/版本 pin/是否 mem_limit） | — |
| T-M14-00 | Opus | 文件先行：D12 + §1/§5/D5/D10 + roadmap + CLAUDE.md（模型名可後補） | 與 00 並行 |
| T-EM-01 | Sonnet | local_embed.py + llm 分派 + effective_embed_config + config/settings/compose volume + requirements；單元測試（懶載入、分派路由、維度 assert、auto 矩陣） | 00, M14-00 |
| T-DG-01 | Sonnet | chat_once + with_tools 參數（agent/claude_backend）+ digest 改呼叫；測試遷移（含 Claude 風格回覆樣本） | M14-00（**全程與 A/C 並行**） |
| T-EM-02 | Sonnet | reembed 服務 + maintenance router + 鎖共用；測試（互斥/進度/失敗續跑） | EM-01 |
| T-BK2-01 | Opus | 備份匯出 v2：dump_chunks + chunk 檔 + manifest v2 + codec；測試（roundtrip/snapshot） | EM-01 |
| T-BK2-02 | Opus | 還原 v2：三路分派 + merge 插 chunk + chunk_id remap + 修復升級 + v1 相容；test_restore 擴充（直灌斷言零 embed 呼叫等） | BK2-01 |
| T-EM-03 | Sonnet | 前端：embedding 來源 segmented + 切換警告 + 重建按鈕與進度；client.ts/i18n | M14-00（與後端並行） |
| T-M14-90 | Haiku | README 本地模式說明（首次下載需網路/RAM/volume）+ .env.example + roadmap 勾選 | EM-01 |
| T-M14-99 | Opus | 整合驗證：pytest/ruff/build 全綠；**eval_citations 雙來源**（local/nim 各一輪，鐵律 1）；**擱置的「清空 DB → 匯入」E2E 一次測三路**（v2 直灌斷言零 embedding 呼叫 + chip 跳轉 + 標註 chunk_id 非 NULL／改 manifest 強制不符走重嵌／真 v1 舊備份相容）；reembed E2E；RAM 峰值觀測 | 全部 |

## 驗證 / Verification

1. `py -m pytest`（backend 目錄；既有 241 不退化）、`ruff check`、`docker compose exec web npm run build`。
2. 煙霧測試決策記錄落 D12（模型、RAM、速度數字）。
3. 三路還原 E2E（見 T-M14-99）；同庫第二次匯入摘要全 0（冪等不退化）。
4. eval_citations：local 與 nim 兩種 embed_source 各跑一輪不退化（15/15 基準）。
5. 純 Claude 情境驗收：`.env` 拿掉 EMBED_API_KEY + chat_backend=claude-sdk → 上傳/提問/導讀/備份/還原全功能可用（零 NIM 依賴）。

## 風險 / Risks

| 風險 | 緩解 |
|---|---|
| 模型首次下載需網路（數百 MB） | volume 快取一次性；下載失敗拋 LLMError 走既有 failed 狀態機可重試；README 明示 |
| RAM 峰值未實測（int8 估 0.7–1.2GB） | T-EM-00 先行實測拍板變體；必要時 compose mem_limit + 文件標最低記憶體 |
| 混模型向量共存污染檢索 | 切換警告 + 一鍵重建；reembed 與 backup/restore 同鎖，防半新半舊被 dump |
| SQLite 測試環境無 vector 型別 | dump_chunks 等測試比照既有 Postgres parity 慣例（monkeypatch 或標記）；T-M14-99 真 Postgres E2E 把關（M13 教訓） |
| v2 備份體積（百篇 ~55MB/輪全量） | 單機夜間備份可容忍；manifest chunk_files 已備妥未來增量的資訊面 |
| digest 改走 Claude 後 extract_json 容錯 | 既有圍欄容錯 + T-DG-01 樣本測試 + 整合實跑一篇導讀 |
