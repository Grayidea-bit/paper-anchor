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

### D2 Chunking 策略
- 以版面結構切分：優先依標題/段落邊界，目標 400–600 tokens，重疊 80 tokens。
- 保留 `chunk_index` 順序，支援「取前後相鄰 chunk」擴充上下文（選取提問時用）。

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
- 預設模型：chat `deepseek-ai/deepseek-v3.1`、embed `nvidia/llama-3.2-nv-embedqa-1b-v2`（2048 維，`EMBED_DIM` 需同步 schema 的 VECTOR 維度）。模型名以 .env 為準，程式碼不得寫死。

## 4. 資料模型

```sql
users        (id, email, created_at)                    -- MVP 單一預設 user，預留擴充
documents    (id, user_id, title, filename, file_path, page_count,
              status, error_msg, digest JSONB, token_usage JSONB, created_at)
chunks       (id, document_id, chunk_index, page, section,
              content TEXT, bbox_list JSONB, embedding VECTOR(2048))  -- 維度=EMBED_DIM
conversations(id, document_id, title, created_at)
messages     (id, conversation_id, role, content TEXT,
              citations JSONB,      -- [{chunk_id, page, bbox_list}]
              selection JSONB,      -- 選取提問時的 {text, chunk_id}
              token_usage JSONB, created_at)
```
索引：`chunks(document_id)`、`chunks USING ivfflat (embedding vector_cosine_ops)`、`messages(conversation_id)`。

## 5. API 規格（摘要）

| Method | Path | 說明 |
|---|---|---|
| POST | /api/documents | 上傳 PDF（multipart），回 document + status |
| GET | /api/documents | 文獻列表 |
| GET | /api/documents/{id} | 詳情（含 status/digest，供輪詢） |
| GET | /api/documents/{id}/file | 取 PDF 原檔（供 PDF.js 渲染） |
| DELETE | /api/documents/{id} | 刪除（含 chunks/conversations 級聯） |
| GET | /api/documents/{id}/conversations | 對話串列表 |
| POST | /api/documents/{id}/conversations | 建立對話串 |
| GET | /api/conversations/{id}/messages | 歷史訊息 |
| POST | /api/conversations/{id}/messages | 送出提問，回應為 SSE 串流 |

SSE 事件格式：`event: token`（增量文字）、`event: citation`（結構化引用）、`event: done`（含 token_usage）、`event: error`。

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
