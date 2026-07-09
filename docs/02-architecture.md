# 技術架構 — AI 文獻導讀

> 角色：架構師
> 狀態：v1.0（2026-07-04）

## 1. 技術選型與理由

| 層 | 選型 | 理由 |
|---|---|---|
| 後端 | Python 3.11 + FastAPI | 團隊既有慣用；async 適合 LLM 串流；生態成熟 |
| 前端 | Vite + React + TypeScript + PDF.js | PDF.js 是唯一成熟的網頁 PDF 渲染方案，支援文字層座標（引用高亮的基礎） |
| 資料庫 | PostgreSQL 16 + pgvector | 單一 DB 同時存業務資料與向量，省掉獨立向量庫的運維 |
| PDF 解析 | PyMuPDF (fitz) | 快、可取得每個文字 span 的頁碼與 bbox 座標 |
| LLM | **NVIDIA NIM**（OpenAI 相容 API），供應商可抽換 | 使用者已有 NIM API key；chat 與 embedding 同一把 key 搞定 |
| Embedding | NVIDIA NIM retrieval 模型（`llama-3.2-nv-embedqa-1b-v2`，2048 維） | 與 chat 同供應商；介面抽象保留抽換空間 |
| 部署 | Docker Compose（api + db + web） | 一鍵啟動、環境一致 |

**刻意不用**：LangChain/LlamaIndex（抽象層過重，RAG 流程自己寫 <300 行更可控）、獨立向量資料庫、訊息佇列（MVP 用 FastAPI BackgroundTasks，P1 若解析吃重再換）。

## 2. 系統架構

```
┌─────────────── Browser ───────────────┐
│  React SPA                            │
│  ├─ PDFPane（PDF.js + 高亮層）         │
│  └─ ChatPane（SSE 串流 + 引用連結）    │
└──────────────┬────────────────────────┘
               │ REST + SSE
┌──────────────▼────────────────────────┐
│  FastAPI                              │
│  ├─ /api/documents  上傳/解析/導讀     │
│  ├─ /api/chat       RAG 對話（SSE）    │
│  ├─ ingest pipeline 解析→chunk→embed  │
│  └─ llm.py          供應商抽象層       │
└───────┬───────────────────┬───────────┘
        │                   │
┌───────▼────────┐   ┌──────▼──────────┐
│ PostgreSQL     │   │ LLM Provider    │
│ + pgvector     │   │ (DeepSeek 預設) │
│ 業務資料+向量   │   └─────────────────┘
└────────────────┘
檔案儲存：本機 volume ./data/uploads（MVP），介面抽象以便換 S3
```

### 部署假設（deployment assumptions，M15）

本系統以**單機、單使用者、可信環境**為前提設計，兩條假設寫死在架構中，部署者須知悉：

1. **單一 uvicorn worker / process**：backup/restore/reingest 的互斥鎖與 `settings_store` 設定快取皆為**模組級（per-process）狀態**，不跨行程共享。若以多 worker（`--workers N` 或多副本）啟動，各 worker 各持一份鎖與快取——併發互斥失效（可能同時跑兩份備份／還原）、設定更新只對接到請求的那個 worker 生效。故正式部署維持單 worker；lifespan 啟動時會偵測 worker 數並在多於一個時記警告日誌。
2. **預設信任網段（API 無認證）**：API 本身不做身分驗證，安全模型倚賴網路邊界——`docker compose` 將對外埠綁定 `127.0.0.1`（僅本機，M15 T-FD-04），DB 埠不對外暴露。秘密（LLM key、OAuth token 等）存於 DB 的 `settings` 表（`settings_store` 白名單鍵，SECRET_KEYS 遮罩），不入 repo、不隨備份出庫（鐵律 6）。若需跨機存取，應以 SSH port-forward 或反向代理加認證，而非直接開放埠。

## 3. 核心設計決策

### D1 引用錨點機制（本產品的靈魂）
- 解析 PDF 時，每個 chunk 記錄 `page`、`bbox_list`（該 chunk 內各文字區塊的座標）、`section`（若可判斷）。
- RAG 上下文送給 LLM 時，每個 chunk 前綴穩定 ID：`[C12]`。
- System prompt 要求 LLM 引用時輸出 `[C12]` 標記；後端把標記轉換為結構化引用（chunk_id → page + bbox）隨 SSE 送出。
- 前端把 `[C12]` 渲染成可點擊引用 → PDF.js 跳頁 → 依 bbox 疊加高亮層。
- **驗收**：引用點擊命中率是整合測試的必測項。

### D2 Chunking 策略（M1 實作後修訂）
- 以 PyMuPDF 的版面 block 為最小單位聚合，chunk 不跨頁（bbox 高亮以頁為錨），目標 ~1800 字元、上限 2400（≈500 tokens，受 embedding 模型 512 token 上限約束，API 另帶 `truncate:"END"` 保險）。
- 不做內容重疊：檢索時以「取前後相鄰 chunk」擴充上下文補償（`chunk_index` 連續）。
- 表格/圖說明歸入所在位置的 block 流；空 block、純空白跳過。

### D3 RAG 對話流程
1. 使用者提問（可附帶 selection：選取文字 + 所在 chunk_id）。
2. 檢索：question embedding → pgvector cosine top-8；若有 selection，強制加入該 chunk 及前後各 1 個。
3. 組 prompt：文獻 metadata + 導讀摘要（全域上下文）+ 檢索 chunks + 對話歷史（最近 10 輪）。
4. 串流回覆（SSE），結束後把 assistant 訊息與引用、token 用量入庫。

### D4 導讀生成
- 上傳解析完成後 BackgroundTask 觸發，結果為結構化 JSON（研究問題/方法/發現/貢獻/限制，各含 `cited_chunks`），存 `documents.digest`。
- 長文獻（>100 chunks）採 map-reduce：分段摘要 → 合併。
- 文件狀態機：`uploaded → parsing → embedding → digesting → ready | failed(error_msg)`，前端輪詢 `GET /api/documents/{id}` 顯示進度。
- **啟動時 reconciliation（M15 T-FD-01）**：程序被殺（重啟／OOM／`--reload`）會使 ingest 中途文獻永久卡在 `parsing`/`embedding` 這類 transient 殘態。lifespan 啟動時把所有卡在 transient 狀態的文獻視為「上一輪被中斷」，一律重置為 `failed`（帶 `error_msg`），使其可被重跑而非永久黑洞。
- **ingest 冪等（M15 T-FD-01）**：`ingest_document` 開頭無條件 `delete_chunks`（廉價換冪等），任何重跑（reingest 端點、restore 修復、啟動重置後手動重試）都先清舊 chunks 再重建，不撞 `UNIQUE(document_id, chunk_index)`、不留半殘狀態。
- **手動重跑入口**：`POST /api/documents/{id}/reingest`（見 §5）清舊 chunks 重跑 ingest；文獻不存在回 404；該文獻或全域 backup/restore 操作進行中回 409 `operation_running`。

### D5 NVIDIA NIM 供應商注意事項（llm.py 實作時必讀）
- Chat 與 embedding 都走 `https://integrate.api.nvidia.com/v1`，OpenAI SDK 相容。
- **Embedding 需帶 `input_type` 參數**（OpenAI SDK 用 `extra_body`）：入庫文件用 `"passage"`，查詢問題用 `"query"`——llm.py 的 embed 介面要區分這兩種模式，用錯會顯著拉低檢索品質。
- Embedding 批量與單筆長度有上限，llm.py 內做分批與截斷保護。
- 預設模型：chat `deepseek-ai/deepseek-v4-flash`（NIM 已無 v3 系列；要更高品質換 `deepseek-v4-pro`）、embed `nvidia/nv-embedqa-e5-v5`（1024 維，`EMBED_DIM` 需同步 schema 的 VECTOR 維度）。模型名以 .env 為準，程式碼不得寫死。
- **v4-flash 是推理模型**：回覆會含思考段（reasoning），M2 chat 實作需分離 `reasoning_content`／過濾 `<think>` 段，只呈現最終答案。
- **維度上限**：pgvector 的 ivfflat/hnsw 索引上限 2000 維，選 embedding 模型時不得超過。

### D6 專案分類與 scope 化檢索（M5）
- **單層專案**：文獻屬於 0 或 1 個專案（`documents.project_id` nullable；刪專案 → 文獻回未分類）。
- **對話三態**：`conversations.scope IN ('document','project','library')`，document_id / project_id 互斥 CHECK；專案刪除時專案對話 CASCADE。
- **檢索隔離在 SQL 層**：`repo.similar_chunks_scoped()` 依 scope JOIN documents 硬過濾，service 層無法繞過。多文獻檢索以 `ROW_NUMBER() OVER (PARTITION BY document_id ORDER BY 距離) <= 4` 防單篇洗版，全域 top-12（單篇維持 top-8）。
- **引用標籤改用全域 chunk id**：`[C{chunks.id}]`（三種 scope 統一）。chunk_index 跨文獻撞號；「每請求臨時編號」會讓多輪對話的歷史標籤錯配到新 chunk，否決。citations 結構加 `label`（=標籤數字）、`document_id`、`document_title`；舊訊息（label 缺）由前端以 chunk_index fallback 配對，零資料遷移。
- **selection 提問僅限 document scope**（其他 scope 回 400）；digest 管線不變（單篇語境無撞號）。

### D7 Agent 環境與工具（M7，Pydantic AI；M8 擴展後端分派）
- **對話管線建在 Pydantic AI 上**（`services/agent.py`）：模型每請求以 `llm._chat_config()`（settings 覆蓋 .env）建構 `OpenAIChatModel`；框架事件映射為本專案事件協定（token/reasoning/tool/context_chunks/usage）。llm.py 保留 embeddings（NIM input_type 特規）、digest 用非串流 chat、ThinkFilter、RPM 統計。
- **工具＝複製檔案即註冊**：`app/tools/` 每模組定義 `ENABLED` 與 `TOOLS`（型別註記自動生成 schema、docstring 即模型說明）；`template_tool.py` 為複製模板。內建 `keyword_search`（scope 隔離 ILIKE 檢索）。
- **工具結果可引用**：工具回 `ToolReturn(return_value=帶 [C{id}] 文字, metadata={"chunks": [...]})`，router 把 chunks 併入引用對照表——模型引用工具找到的段落照樣可點擊跳轉高亮。
- **安全底線**：無啟用工具 → 不帶 toolsets，管線行為與純串流一致。**降級保險**：帶工具請求在未輸出前收到 4xx → 剝除工具重試（防供應商不支援 function calling）。輪數上限 `UsageLimits(request_limit=5)`（含首輪＝最多 4 輪工具）。
- **執行期設定**（settings 表 + `settings_store` 快取）：chat 的 base_url/api_key/model、附加 system prompt；API key 永不回傳明文。工具過程訊息不入庫（同 reasoning 先例）。

### D8 使用者標註（T-AN-01；T-AN-06 擴展工具）
- **錨定設計**：前端把 DOM selection（滑鼠/觸控選取）換算成 PDF 座標系的 bbox（四元組 `[x0,y0,x1,y1]` in PDF points），連同頁碼、type/color/text 存 DB（`annotations` 表）。bbox 渲染時同 citation 機制乘以頁面縮放係數疊加高亮層，不碰 chunks 引用鏈（鐵律 1）。
- **顏色策略**：存語義 key（`'amber'|'terracotta'|'sage'|'slate'`），由前端主題系統解析為實際 RGB，支援深色模式。
- **chunk 引用加值**：optional `chunk_id` 指向最相關 chunk（AI 工具用），刪 chunk 時 SET NULL 不連坐（annotations 自立存在）。
- **CRUD 端點**（T-AN-01）
  - `GET /api/documents/{id}/annotations` — 列表（按 page 與 created_at 排序）
  - `POST /api/documents/{id}/annotations` — 建立 → 201
  - `PATCH /api/annotations/{id}` — 部分更新（note_text/color），touch updated_at
  - `DELETE /api/annotations/{id}` → 204
- **AI 工具化**（T-AN-06）：`repo.list_annotations_scoped(document_id=*, project_id=*, type_filter=*, limit=50)` 為工具提供 scope 隔離查詢；結果可含全文搜尋、統計等擴展。

### D9 翻譯表（glossary）（T-TR-01 / T-TR-04）
- **錨定設計同 annotations**：使用者於 PDF 圈選術語 →「加入翻譯表」，前端換算 bbox（PDF 座標系）連同頁碼、chunk_id 送後端；`glossary_entries` 表結構與 annotations 同源（`page`/`bbox_list`/`chunk_id`），不碰 chunks 引用鏈本身（鐵律 1）。
- **目標語言設定**：`settings_store` 白名單鍵 `translation_target_lang`（非 secret），值為顯示用字串（如「繁體中文」「English」「日本語」）直接進 prompt；未設定時服務層回落預設 `"繁體中文"`。
- **建立優先序（T-TR-04 / T-TR-06）**：
  1. **前端直接提供譯文（T-TR-06，最高優先）**：POST 帶 `translation` 欄位（max 500 字）時直接存庫，不打 LLM；`notes` 可同時提供（max 12000 字，無則預設空字串）。適用「翻譯回答抽取」流程：前端自行從翻譯結果第一行抽 translation、整份回答當 notes，加速條目建立。
  2. **從對話萃取（主路徑）**：POST 帶 `source_text`（對話「翻譯」動作的詳細翻譯全文，max 8000 字）但無 `translation` 時，`services/glossary.py` 套用 `app/prompts/glossary_extract.md`（帶術語、目標語言、`source_text`）→ 呼叫 `llm.chat()` → 解析固定格式「譯文：/註解：」兩行 → `translation` + `notes` 入庫。解析失敗（LLM 未照格式回覆）降級：整段 `strip()` 當 `translation`，`notes` 存空字串。
  3. **直接圈選加入（fallback）**：不帶 `source_text` 且無 `translation` 時行為與 T-TR-01 相同——若有 `chunk_id` 撈該 chunk 內容截前 800 字當上下文 → 套用 `app/prompts/translate_term.md` → 呼叫 `llm.chat()`（既有非串流 helper，鐵律 3；未新增 llm.py 函式）→ 譯文 `strip()` 後存庫，`notes` 存空字串。
- **失敗降級**：LLM 呼叫失敗時條目仍建立（或 retranslate 時保留舊譯文），`translation`/`notes` 存空字串，不擲例外、不讓請求 500；前端可用 retranslate 端點重試或換目標語言後重打（retranslate 僅重打 `translation`，`notes` 不動）。
- **CRUD 端點**
  - `GET /api/documents/{id}/glossary` — 條目列表（按 page 與 created_at 排序）
  - `POST /api/documents/{id}/glossary` — 建立條目 → 201（含首次翻譯結果；body 可選 `source_text` 觸發從對話萃取路徑）
  - `POST /api/glossary/{entry_id}/retranslate` — 重打一次翻譯並更新（僅更新 `translation`）
  - `DELETE /api/glossary/{entry_id}` → 204

#### 後端分派（M8：Claude Agent SDK 後端）
- **設定鍵 `chat_backend`**：settings 表值為 `'openai'`（NIM／OpenAI 相容，預設）或 `'claude-sdk'`（Claude Agent SDK）；使用者於設定頁切換。
- **路由分派**：`agent.stream_chat()` 開頭讀 `chat_backend`：`'openai'` 續用既有 Pydantic AI 路徑（一行不動）；`'claude-sdk'` 委派 `services/claude_backend.py`，把 SDK 訊息/事件流映射為同一協定（token/reasoning/tool/context_chunks/usage）——router/SSE/引用鏈/前端零改動。範圍僅 chat；digest/embedding 續走 llm.py（Claude 無 embedding API）。
- **工具橋接（Claude 後端）**：`tools.build_sdk_mcp_server(deps, sink)` 把 app/tools/ 同批函式包成 SDK `@tool` + `create_sdk_mcp_server`（server name `anchor`）；`ToolReturn.metadata["chunks"]` 走 per-request contextvars 側信道（`sink`）傳回，claude_backend 在每次 tool done 後吐 `context_chunks`——引用協定兩後端一致。
- **安全鎖定**：ClaudeAgentOptions 一律 `tools=[]` + `setting_sources=[]` + `allowed_tools=["mcp__anchor__*"]`，`system_prompt` 用純字串（完全取代 Claude Code 提示詞），實測模型無任何內建工具（Bash/Read/Write）；token 放 `options.env["CLAUDE_CODE_OAUTH_TOKEN"]`，容器絕不設 ANTHROPIC_API_KEY（優先序會蓋過）。
- **登入與付款**：NIM 後端使用者自備 API key；Claude 後端使用者以官方 `claude setup-token` 產出一年效期 token 貼入設定頁，用 Pro/Max 訂閱額度，無需另購 API credits。**不內建逆向 OAuth 端點**（未官方公開、有授權風險；見 roadmap M8）。

#### 模型選擇（M9：每對話單獨設定）
- **二層結構**：後端選項（settings `chat_backend` = `'openai'`|`'claude-sdk'`）決定**可選模型清單源頭**，模型選擇（每對話 `conversations.model`）決定**該對話用哪個模型**。
- **模型清單來源**
  - **NIM/OpenAI 後端**：使用者在設定頁填 settings 鍵 `llm_chat_models`（字串陣列，e.g. `["deepseek-v4-flash", "deepseek-v4-pro"]`）；對話區下拉顯示該陣列全部；首元素作為 `llm._chat_config()` 預設（digest/healthz 用同一鏈）。
  - **Claude SDK 後端**：內建固定版本號清單 `app/models_catalog.py` 的 `CLAUDE_MODELS = ["claude-sonnet-5", "claude-opus-4-8", "claude-haiku-4-5"]`；前端靜態對應 `{value, label}` 映射；全部版本預先以訂閱 token 探測可用，不支援使用者自訂。版本更新時後端程式碼變動。
- **資料持久化**：Migration `004_conversation_model.sql` 加 `conversations.model TEXT` 欄（NULL=未指定）。新 PATCH `/api/conversations/{id}` 取得 `{model: string}` 更新該欄。send_message 讀該欄：若不為空送 `agent.stream_chat(model=...)`；否則用 llm 預設鏈決定。
- **模型校驗與回落**
  - **單一 choke point**：`agent.stream_chat` 開頭呼叫 `_resolve_model(backend: str, model: str|None, available_models: list[str]) -> str` 進行校驗與回落。
  - **允許清單校驗**：`backend == 'claude-sdk'` 時允許 = `CLAUDE_MODELS`；`backend == 'openai'` 時允許 = 使用者的 `llm_chat_models`（settings 項，若空陣列或不存則用 env 單一值）。
  - **無效或 None 時回落**：若 `model is None` 或不在允許清單，靜默回落該後端預設（不報錯；digest/healthz 等機制無感呼叫）。回落後的 model 傳入 OpenAI SDK（`OpenAIChatModel(model_override=model)`）或 Claude backend（`_build_options(model=model)`）。
- **前端行為**：對話區下拉列表值繫結 `conversations.model`；切換時 PATCH 端點寫 DB；支援「無選擇」→回落流程。

### D10 單向備份到 Google Drive（M12）

#### 技術方案取捨（rclone vs Drive REST API）
- **方案 A（rclone 容器內 binary）／方案 B（rclone + 產 conf）／方案 C（Drive REST API，httpx 直打）**。**使用者拍板方案 C**。
- **選 C 的理由**：單向備份用不到 rclone 的強項（bisync 雙向、多供應商），但其成本全在——Docker image 裝 binary、產生與維護 `rclone.conf`、rclone 刷新 token 時會改寫 conf 造成雙份 token 狀態、需解析 rclone JSON log 取進度、測試得 mock subprocess。而 OAuth flow 無論哪個方案都得自己寫，rclone 省不掉。方案 C 零新依賴（沿用既有 httpx）、狀態全在 settings 表、可直接單元測試。
- **rclone 記為已驗證 fallback**：已驗證我們 OAuth 拿到的 token JSON 可直接塞進 `rclone.conf` 的 `[gdrive]` 段（`token = {...}`）驅動 rclone。保留為未來擴充多供應商（S3/OneDrive 等）的驗證過路徑。**兩個坑**須注意：(1) rclone 刷新 access token 時會回寫 conf，與 settings_store 形成雙份可寫狀態，需擇一為權威；(2) conf 內的 scope 必須與我們授權時的 `drive.file` 一致，否則 rclone 會拿舊 scope 重新授權。
- **供應商存取邊界**：Drive 存取一律收束在 `services/gdrive.py`（OAuth + REST 4 函式窄介面）；換 rclone 實作只動這一層，`services/backup.py` 以上不變。

#### 匯出格式 v1（`format_version: 1`）
Drive 遠端佈局（**單一鏡像，非快照制**）：

```
PaperAnchor Backup/
  manifest.json          ← 最後上傳（原子性標記：有它才算一次完整備份）
  db/
    documents.json  projects.json  annotations.json
    glossary_entries.json  conversations.json  messages.json
    settings.json        ← 僅非 SECRET_KEYS 鍵（API key／token 絕不出庫，鐵律 6）
  pdfs/
    {uuid}.pdf           ← 增量 append-only：遠端已存在同名即跳過
```

- **PDF**：保留 UUID 檔名（原始檔名存於 `documents.json` 的 `filename` 欄）；直接從 `/data/uploads` 串流上傳，不落地複製。增量策略：先列遠端 `pdfs/`，同名即跳過（append-only）。
- **DB dump**：每次全量覆蓋（Drive `files.update` 同一 file id）；dump 僅在暫存目錄 `/data/backup_staging/` 落地，上傳後清除。白名單表、明確欄位、`datetime → isoformat`；`settings.json` 只含非 SECRET_KEYS 鍵。
- **不備份 `chunks`／`embedding`**（可由 PDF 重建）；`manifest.json` 記 `embed_model`／`embed_dim` 供未來還原判斷是否需重嵌。
- **manifest 結構**：`{format_version, created_at, app_version, embed_model, embed_dim, counts{documents, projects, annotations, glossary_entries, conversations, messages, pdfs}, pdfs: [{name, document_id, size}]}`。上傳順序：先 PDF、再 `db/`、**最後 manifest**——任一步失敗即中止本輪且不上傳 manifest。保證限縮如下：manifest 存在＝一次完整備份完成的標記；`db/` dumps 為全量覆蓋（`files.update` 同一 file id，中途失敗時遠端可能是「新 db + 舊 manifest」，**不保證與 manifest counts 逐筆一致**）；`pdfs/` 為 append-only，舊 manifest 的 `pdfs` 清單永遠是遠端現存檔案的子集，故**依 manifest 還原永遠一致**。

#### OAuth 設計（loopback）
- **使用者自建 Desktop app client**：於 Google Cloud Console 建自己的 OAuth client（型別 Desktop app），把 client_id／client_secret 貼進設定頁。
- **不提供共用 OAuth app**：client 憑證入公開 repo 違反鐵律 6；所有部署者共吃一個 client 的 API 配額；app 維運與 Google 審查責任落到專案作者身上。rclone 內建共用 client 被限流、官方建議自建，即為前例。
- **Scope**：`https://www.googleapis.com/auth/drive.file`（最小權限、非敏感、免 Google 審查）。
- **Flow**：backend 自行實作 loopback。`redirect_uri = http://localhost:8000/api/backup/auth/callback`（Desktop client 允許任意 localhost port，callback 就是普通 FastAPI route）。帶 `state`（CSRF 防護）+ PKCE（code_challenge/verifier）；`access_type=offline&prompt=consent` 換取 refresh token。
- **Token 存放**：refresh token 存 `settings_store`（`gdrive_refresh_token`，列入 SECRET_KEYS 遮罩，沿用 `claude_oauth_token` 先例）；access token 只存記憶體、過期即以 refresh token 換新。`invalid_grant`（refresh token 失效）→ 服務層回報斷線、前端提示重新連接。

#### 刪除語意
- 備份**非同步**（backup ≠ sync）：本機刪除文獻**不會**刪除遠端對應 PDF 或 DB dump 內容。遠端 `manifest.json` 的 `pdfs` 清單即該次備份的「現存集合」，還原時以 manifest 為準；遠端殘留的舊 PDF 不影響還原正確性。

#### 新增 settings 鍵（M12）
沿用 `settings` 表（白名單鍵，`settings_store` 快取）：

| 鍵 | 說明 | Secret |
|---|---|---|
| `gdrive_client_id` | 使用者 Desktop app OAuth client id | 否 |
| `gdrive_client_secret` | OAuth client secret | **是**（SECRET_KEYS 遮罩） |
| `gdrive_refresh_token` | OAuth refresh token（callback 取得後寫入） | **是**（SECRET_KEYS 遮罩） |
| `backup_interval_hours` | 定時備份間隔小時數（0＝關閉） | 否 |
| `backup_last_run` | 上次備份時間與結果摘要（服務層寫入，PUT 不開放） | 否 |
| `restore_last_run` | 上次還原時間與結果摘要（服務層寫入，PUT 不開放；M13，見 D11） | 否 |

> `gdrive_client_id`／`gdrive_client_secret`／`backup_interval_hours` 須同步加入 `routers/settings.py` 的 `SettingsUpdate`（漏加會 PUT 靜默丟棄，見 M11 發現事項）；`gdrive_refresh_token`／`backup_last_run`／`restore_last_run` 不開放 PUT，列入守護測試 WRITE_EXEMPT。

### D11 從 Google Drive 匯入還原（M13）

M12 只做單向備份並預留 `format_version: 1`；M13 補上反向的**匯入還原**（restore）：把遠端 manifest 指向的一次完整備份合併回本機 DB，並對新文獻重跑 ingest（解析→切塊→嵌入）以重建引用鏈。**還原是合併，不是覆蓋整庫**——設計目標是「新機還原可完整重現、舊機重跑不破壞本地較新資料、任何中斷重跑都收斂」。

#### 合併規則總原則
不刪本地任何列；所有主鍵（id）在插入時重生並在關聯欄位 remap；可比較時間戳時新者勝、無從比較時本地優先；`settings.json` 一律不還原。每張表以**內容身分簽章**（非備份端 id）判斷「本地是否已存在對應列」：

| 表 Table | 身分簽章 Identity | 已存在 If exists | 不存在 If absent |
|---|---|---|---|
| projects | `name` | remap id，不改欄位 | 插入（顯式 `created_at`） |
| documents | PDF UUID 檔名（`file_path` 的 basename，即上傳時生成的 `{uuid}.pdf`；非 `filename` 原始檔名——後者不唯一） | remap id，整篇跳過（不重嵌、不覆蓋） | 新文獻流程（下述） |
| annotations | (document, type, page, bbox_list 簽章) | 比 `updated_at`：備份較新→覆蓋 `note_text`/`color`/`selected_text`；否則跳過 | 插入（顯式時間戳，`chunk_id=NULL`） |
| glossary_entries | (document, term, target_lang, page) | 跳過（無 `updated_at` 可比） | 插入（`chunk_id=NULL`） |
| conversations | (scope, remap 後目標, title, created_at) | 整串跳過（含 messages） | 整串匯入（保留 `model`） |
| settings.json | — | **一律不還原**（機器組態非內容） | — |

- **citations JSONB（messages）**：只 remap `document_id`（查無對應→`null`，chip 顯示但不可跳、不誤跳）；`label`/`chunk_id`/`chunk_index` 原樣保留（訊息內自洽）。跳轉只靠 `page`+`bbox_list`（見 D1），不受 id 重生影響。
- **annotations／glossary 的 `chunk_id`（真 FK）**：插入時一律 `NULL`。備份端的舊 `chunk_id` 在本機必為 FK violation（chunks 由 ingest 重生、id 全新）；標註本身自帶 `page`+`bbox_list`，不依賴 `chunk_id` 定位。
- **新文獻流程**：Drive 下載 PDF 到 `upload_dir` → `restore_insert_document`（寫入 dump 的 `digest`/`token_usage` 與顯式 `created_at`）→ **同步逐篇** `ingest_document(id, run_digest=(dump 無 digest))`。dump 已有 `digest` 就沿用、`run_digest=False` 跳過重生成（省下最貴的 LLM 呼叫；digest 內 citations 自洽於 `page`+`bbox_list`）；dump 無 digest 才 `run_digest=True` 重跑。逐篇序列執行（非平行），兼作進度 `ingest n/m` 並避免同時重嵌打爆 embedding API。
- **缺 PDF 的文獻**：遠端 `pdfs/` 找不到對應檔的文獻，整篇連同其標註/翻譯表/對話一併跳過，記入 summary 的 `documents_skipped`（無 PDF 無法重建 chunks，勉強插入只會得到殘缺文獻）。
- **單篇 ingest 失敗續跑 + 重跑即修復（冪等收斂保證）**：任一篇 ingest 失敗時標記該文獻 `status=failed`、繼續處理其餘篇、summary 記入 `ingest_failed:[title]`，不中止整輪。**重跑 restore 即修復**：既存文獻若匹配到 `failed` 狀態，先 `delete_chunks` 清殘塊再重嵌；正常文獻整篇跳過。因此無論中途伺服器重啟留下半還原、或個別篇因限流失敗，重跑都會朝「全庫齊備」收斂，整體操作冪等。
- **format_version 檢查**：讀 manifest，`format_version != 1` → `400 unsupported_format`；`embed_model` 不比對（新文獻一律以本機當前 embedder 重嵌，故不需相容）。
- **鎖互斥**：restore 與 backup **共用同一把服務層鎖**（`backup.py` 抽 `try_begin(operation)`／`set_progress` helper，`get_status()` 讀 `_operation`）。一把鎖天然互斥——備份進行中觸發還原回 `409 operation_running`，反之亦然；常駐排程（M12 scheduler）零改動即被同一把鎖擋下。
- **持久化與 summary**：還原結束把結果寫入 `restore_last_run`（settings 鍵，服務層寫入、PUT 不開放）。summary 結構：

  ```
  {documents_new, documents_skipped, annotations_new, annotations_updated,
   glossary_new, conversations_new, messages_new, ingest_failed: [title, ...]}
  ```

#### 模組落點
新檔 `backend/app/services/restore.py`（合併引擎 + `run_restore` 編排）；`gdrive.py` 加 `download_file(file_id, dest_path)`（`GET files/{id}?alt=media` 串流落地，走既有 `_authed` 重試）；`repo.py` 加 restore 專用 insert 函式（支援顯式 `created_at`/`updated_at`）與 `restore_overwrite_annotation`／`delete_chunks`（查詢面複用既有 `list_*`）；`ingest.py` 的 `ingest_document` 加 `run_digest: bool = True` 參數（鐵律 1 相鄰，整合階段跑引用鏈回歸）；`routers/backup.py` 加薄端點 `POST /restore`。

## 4. 資料模型

```sql
users        (id, email, created_at)                    -- MVP 單一預設 user，預留擴充
settings     (key PK, value JSONB, updated_at)          -- M7：執行期設定（白名單鍵）
projects     (id, user_id, name, created_at)            -- M5：單層專案
documents    (id, user_id, project_id NULL→未分類, title, filename, file_path,
              page_count, status, error_msg, digest JSONB, token_usage JSONB, created_at)
chunks       (id, document_id, chunk_index, page, section,
              content TEXT, bbox_list JSONB, embedding VECTOR(1024))  -- 維度=EMBED_DIM
annotations  (id, document_id, type, color, page, bbox_list JSONB,
              chunk_id NULL, selected_text, note_text, created_at, updated_at)  -- T-AN-01
glossary_entries (id, document_id, term, translation, target_lang, page,
              bbox_list JSONB, chunk_id NULL, notes TEXT, created_at)         -- T-TR-01 / T-TR-04
conversations(id, scope('document'|'project'|'library'),
              document_id NULL, project_id NULL,        -- 互斥 CHECK 對應 scope
              title, model NULL, created_at)            -- model 為 NULL 時回落
messages     (id, conversation_id, role, content TEXT,
              citations JSONB,      -- [{label, chunk_id, chunk_index, page, bbox_list,
                                    --   document_id, document_title}]
              selection JSONB,      -- 選取提問時的 {text, chunk_id}（僅 document scope）
              token_usage JSONB, created_at)
```

### annotations 表詳解（T-AN-01）
| 欄位 | 型態 | 說明 |
|---|---|---|
| id | BIGINT PK | 自增 ID |
| document_id | BIGINT FK | 所屬文獻（CASCADE） |
| type | TEXT | `'underline'\|'highlight'\|'note'` |
| color | TEXT | `'amber'\|'terracotta'\|'sage'\|'slate'`，存語義 key，前端解析為顏色 |
| page | INT | 所在頁碼（≥1） |
| bbox_list | JSONB | `[[x0,y0,x1,y1], ...]`，PDF 座標系（points），最少一個 |
| chunk_id | BIGINT FK | 可選；指向相關 chunk（SET NULL）——AI 工具用 |
| selected_text | TEXT | 選取的原文（max 3000 字） |
| note_text | TEXT | 使用者註記（max 2000 字）；type='note' 時必填 |
| created_at | TIMESTAMPTZ | 建立時間 |
| updated_at | TIMESTAMPTZ | 最後修改時間（PATCH 時 touch） |

### glossary_entries 表詳解（T-TR-01 / T-TR-04）
| 欄位 | 型態 | 說明 |
|---|---|---|
| id | BIGINT PK | 自增 ID |
| document_id | BIGINT FK | 所屬文獻（CASCADE） |
| term | TEXT | 使用者圈選的原文術語 |
| translation | TEXT | LLM 譯文（術語層級）；LLM 失敗或解析失敗時為空字串，可 retranslate 重試 |
| target_lang | TEXT | 建立當下的目標語言（顯示字串，如「繁體中文」），換設定不回溯改舊條目 |
| page | INT | 所在頁碼（≥1） |
| bbox_list | JSONB | `[[x0,y0,x1,y1], ...]`，PDF 座標系（points），最少一個 |
| chunk_id | BIGINT FK | 可選；指向所在 chunk（SET NULL），供翻譯上下文與未來 AI 工具用 |
| notes | TEXT | 一到兩句白話補充說明（T-TR-04）；僅當建立時帶 `source_text` 且 LLM 成功依格式回覆才有值，否則為空字串；`retranslate` 不會更動此欄 |
| created_at | TIMESTAMPTZ | 建立時間 |

索引：`chunks(document_id)`、`messages(conversation_id)`、`annotations(document_id)`、`glossary_entries(document_id)`。

> **刻意未建 ANN 向量索引（M15 校正）**：migration 001 未建 ivfflat/hnsw，向量檢索走精確掃描。此決策**僅 document scope 成立**——單篇文獻的 chunk 數少，全表精確掃描比 ANN 更準也夠快；但 **library/project scope 為全庫精確掃描**，成本隨 chunk 總數線性上升。**chunk 總數破 ~2 萬時應建 HNSW/ivfflat**（門檻卡見 `docs/03-roadmap.md` M15「明確不做」段，屆時再評估）。

## 5. API 規格（摘要）

| Method | Path | 說明 |
|---|---|---|
| POST | /api/documents | 上傳 PDF（multipart），回 document + status |
| GET | /api/documents | 文獻列表 |
| GET | /api/documents/{id} | 詳情（含 status/digest，供輪詢） |
| GET | /api/documents/{id}/file | 取 PDF 原檔（供 PDF.js 渲染） |
| DELETE | /api/documents/{id} | 刪除（含 chunks/conversations 級聯） |
| POST | /api/documents/{id}/reingest | 重新解析文獻（清舊 chunks 重跑 ingest）→ 202；文獻不存在 404；該文獻或全域操作進行中 409 `operation_running`（M15，見 D4） |
| GET | /api/documents/{id}/annotations | 文獻標註列表（T-AN-01） |
| POST | /api/documents/{id}/annotations | 建立標註 → 201（T-AN-01） |
| PATCH | /api/annotations/{id} | 更新標註 {note_text?, color?}（T-AN-01） |
| DELETE | /api/annotations/{id} | 刪除標註 → 204（T-AN-01） |
| GET | /api/documents/{id}/glossary | 文獻翻譯表列表（T-TR-01） |
| POST | /api/documents/{id}/glossary | 建立翻譯表條目 → 201；優先序（T-TR-06）：`translation` 欄位提供時直存（不打 LLM），否則看 `source_text`（萃取譯文+註解）或直翻；body 可選 `translation`（max 500）、`notes`（max 12000）、`source_text`（max 8000）（T-TR-01 / T-TR-04 / T-TR-06） |
| POST | /api/glossary/{entry_id}/retranslate | 重打一次翻譯並更新（T-TR-01） |
| DELETE | /api/glossary/{entry_id} | 刪除翻譯表條目 → 204（T-TR-01） |
| GET | /api/documents/{id}/conversations | 對話串列表（document scope） |
| POST | /api/documents/{id}/conversations | 建立對話串（document scope） |
| PATCH | /api/documents/{id} | 指派/移出專案 {project_id: int\|null} |
| GET | /api/projects | 專案列表（含 document_count） |
| POST | /api/projects | 建立專案 {name} |
| PATCH | /api/projects/{id} | 改名 {name} |
| DELETE | /api/projects/{id} | 刪除（文獻回未分類、專案對話級聯刪除） |
| GET/POST | /api/projects/{id}/conversations | 專案級對話 |
| GET/POST | /api/library/conversations | 全庫級對話 |
| GET | /api/conversations/{id}/messages | 歷史訊息 |
| POST | /api/conversations/{id}/messages | 送出提問，回應為 SSE 串流（依 conv.scope 檢索） |
| GET/PUT | /api/settings | 執行期設定（api_key 遮罩為 `llm_api_key_set`；缺席=不變、空字串=清除） |
| GET | /api/tools | 已註冊 LLM 工具清單（唯讀） |
| GET | /api/usage | 累計 token + `rpm`（最近 60 秒 LLM 請求數） |
| GET | /api/backup/status | 備份狀態（M12，見 D10） |
| POST | /api/backup/run | 觸發立即備份 → 202；進行中回 409 `backup_running`（M12） |
| GET | /api/backup/auth/start | 取 Google OAuth 授權網址（M12） |
| GET | /api/backup/auth/callback | OAuth loopback 回呼，換 token 後存庫（M12） |
| POST | /api/backup/auth/disconnect | 中斷連接、清除 refresh token → 204（M12） |
| POST | /api/backup/restore | 從 Drive 匯入還原 → 202；進行中回 409 `operation_running`（M13，見 D11） |

### 備份端點詳解（M12 / T-BK-03，見 D10；M13 restore 見 D11）

- `GET /api/backup/status` → `200 {connected: bool, running: bool, operation: "backup"|"restore"|null, progress: {phase, current, total}|null, last_run: {at, ok, error?, counts?}|null, last_restore: {at, ok, error?, summary?}|null, interval_hours: int}`。`connected` 依 `gdrive_refresh_token` 是否存在；`running`/`operation`/`progress` 取自 backup 服務模組級狀態（backup 與 restore 共用同一把鎖，`operation` 標示當前進行的操作，見 D11）。`progress.phase`：備份為 `pdfs`/`db`/`manifest`；還原為 `download`/`merge`/`ingest`（`ingest` 階段 `current`/`total` 為第 n／共 m 篇）。`last_run` 為上次備份摘要、`last_restore` 為上次還原摘要（`summary` 結構見 D11），各自持久化於 `backup_last_run`／`restore_last_run`。
- `POST /api/backup/run` → 未進行 `202 {started: true}`；已在跑 `409 {"error": {"code": "backup_running", "message": ...}}`；未連接（無 refresh token）`400 {"error": {"code": "not_connected"}}`。備份於 BackgroundTask 非同步執行，進度改由 `status` 輪詢。
- `GET /api/backup/auth/start` → `200 {auth_url}`（含 state + PKCE challenge，state 暫存於服務層記憶體待 callback 驗證）；未設 `gdrive_client_id` → `400 {"error": {"code": "client_id_unset", "message": "請先填入 Google OAuth client_id"}}`。
- `GET /api/backup/auth/callback?code=&state=` → 驗 state（不符 `400 invalid_state`）→ 以 PKCE verifier 換 token → refresh token 存 `settings_store`（SECRET_KEYS 遮罩）→ 回一頁極簡 HTML「已連接，可關閉此分頁」。此端點供瀏覽器導向，非 JSON API。
- `POST /api/backup/auth/disconnect` → 清除 `gdrive_refresh_token` → `204`。不刪除遠端任何資料（刪除語意見 D10）。
- `POST /api/backup/restore` → 未進行 `202 {started: true}`；已在跑（backup 或 restore）`409 {"error": {"code": "operation_running", "message": ...}}`；未連接（無 refresh token）`400 {"error": {"code": "not_connected"}}`。**端點只做這兩項同步守門**（連接 + 併發）後即排 BackgroundTask 回 202；`no_backup`（遠端無 `manifest.json`）與 `unsupported_format`（`manifest.format_version != 1`）於還原背景執行時偵測，記入 `restore_last_run`／`status.last_restore` 的 `error`（`ok:false`），不另回同步 HTTP 400。還原進度（`download`/`merge`/`ingest`）與結果 summary 一律由 `status` 輪詢；合併規則與冪等保證見 D11。

SSE 事件格式：`event: token`（增量文字）、`event: reasoning`（思考摘要，不入庫）、`event: tool`（工具活動 {name, status}）、`event: citations`（結構化引用）、`event: done`（含 token_usage）、`event: error`。

## 6. 專案結構

```
ai-paper-reader/
├─ CLAUDE.md                 # 開發守則（模型必讀）
├─ docs/                     # 需求/架構/路線圖
├─ docker-compose.yaml
├─ backend/
│  ├─ app/
│  │  ├─ main.py             # FastAPI 入口
│  │  ├─ config.py           # pydantic-settings，讀 .env
│  │  ├─ routers/            # documents.py, conversations.py
│  │  ├─ services/           # ingest.py, rag.py, digest.py
│  │  ├─ llm.py              # 供應商抽象（chat/embed/stream）
│  │  ├─ db/                 # models.py, session.py, migrations/
│  │  └─ prompts/            # 所有 prompt 集中管理（.md 檔）
│  └─ tests/
└─ frontend/
   └─ src/
      ├─ components/PDFPane/  # 渲染 + 高亮層 + 選取選單
      ├─ components/ChatPane/ # 訊息列表 + 引用渲染 + SSE client
      ├─ api/                 # typed API client
      └─ stores/              # 狀態（zustand）
```

## 7. 風險與對策

| 風險 | 對策 |
|---|---|
| PDF 版面千奇百怪，bbox 對不準 | 高亮以「段落級」為準不追求字級；解析失敗 fallback 為純文字模式（無高亮但可對話） |
| LLM 不乖乖輸出 `[C12]` 標記 | prompt few-shot 示範 + 後端正則容錯（`[C12]`/`[c12]`/`C12`）；引用解析失敗時退化為純文字回答 |
| 雙欄 + PDF.js 前端複雜度高 | M1 先做「能渲染能跳頁」，高亮與選取選單拆成獨立任務 |
| Embedding 供應商維度不同 | VECTOR 維度寫入 config，migration 支援重建 |
