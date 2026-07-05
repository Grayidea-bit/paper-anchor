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

### D9 翻譯表（glossary）（T-TR-01）
- **錨定設計同 annotations**：使用者於 PDF 圈選術語 →「加入翻譯表」，前端換算 bbox（PDF 座標系）連同頁碼、chunk_id 送後端；`glossary_entries` 表結構與 annotations 同源（`page`/`bbox_list`/`chunk_id`），不碰 chunks 引用鏈本身（鐵律 1）。
- **目標語言設定**：`settings_store` 白名單鍵 `translation_target_lang`（非 secret），值為顯示用字串（如「繁體中文」「English」「日本語」）直接進 prompt；未設定時服務層回落預設 `"繁體中文"`。
- **翻譯流程**：`services/glossary.py` 讀 target_lang 設定 → 若有 `chunk_id` 撈該 chunk 內容截前 800 字當上下文 → 套用 `app/prompts/translate_term.md` → 呼叫 `llm.chat()`（既有非串流 helper，鐵律 3；未新增 llm.py 函式）→ 譯文 `strip()` 後存庫。
- **失敗降級**：LLM 呼叫失敗時條目仍建立（或 retranslate 時保留舊譯文），`translation` 存空字串，不擲例外、不讓請求 500；前端可用 retranslate 端點重試或換目標語言後重打。
- **CRUD 端點**
  - `GET /api/documents/{id}/glossary` — 條目列表（按 page 與 created_at 排序）
  - `POST /api/documents/{id}/glossary` — 建立條目 → 201（含首次翻譯結果）
  - `POST /api/glossary/{entry_id}/retranslate` — 重打一次翻譯並更新
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
              bbox_list JSONB, chunk_id NULL, created_at)                     -- T-TR-01
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

### glossary_entries 表詳解（T-TR-01）
| 欄位 | 型態 | 說明 |
|---|---|---|
| id | BIGINT PK | 自增 ID |
| document_id | BIGINT FK | 所屬文獻（CASCADE） |
| term | TEXT | 使用者圈選的原文術語 |
| translation | TEXT | LLM 譯文；LLM 失敗時為空字串，可 retranslate 重試 |
| target_lang | TEXT | 建立當下的目標語言（顯示字串，如「繁體中文」），換設定不回溯改舊條目 |
| page | INT | 所在頁碼（≥1） |
| bbox_list | JSONB | `[[x0,y0,x1,y1], ...]`，PDF 座標系（points），最少一個 |
| chunk_id | BIGINT FK | 可選；指向所在 chunk（SET NULL），供翻譯上下文與未來 AI 工具用 |
| created_at | TIMESTAMPTZ | 建立時間 |

索引：`chunks(document_id)`、`chunks USING ivfflat (embedding vector_cosine_ops)`、`messages(conversation_id)`、`annotations(document_id)`、`glossary_entries(document_id)`。

## 5. API 規格（摘要）

| Method | Path | 說明 |
|---|---|---|
| POST | /api/documents | 上傳 PDF（multipart），回 document + status |
| GET | /api/documents | 文獻列表 |
| GET | /api/documents/{id} | 詳情（含 status/digest，供輪詢） |
| GET | /api/documents/{id}/file | 取 PDF 原檔（供 PDF.js 渲染） |
| DELETE | /api/documents/{id} | 刪除（含 chunks/conversations 級聯） |
| GET | /api/documents/{id}/annotations | 文獻標註列表（T-AN-01） |
| POST | /api/documents/{id}/annotations | 建立標註 → 201（T-AN-01） |
| PATCH | /api/annotations/{id} | 更新標註 {note_text?, color?}（T-AN-01） |
| DELETE | /api/annotations/{id} | 刪除標註 → 204（T-AN-01） |
| GET | /api/documents/{id}/glossary | 文獻翻譯表列表（T-TR-01） |
| POST | /api/documents/{id}/glossary | 建立翻譯表條目 → 201，含首次翻譯（T-TR-01） |
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
