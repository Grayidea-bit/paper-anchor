# CLAUDE.md — AI 文獻導讀 開發守則

本專案由多個 Claude 模型（Opus / Sonnet / Haiku）協作開發。**開工前必讀**：
1. `docs/01-requirements.md` — 做什麼、不做什麼
2. `docs/02-architecture.md` — 怎麼做、資料模型、API 規格
3. `docs/03-roadmap.md` — 現在做到哪、你被指派什麼

## 專案一句話

雙欄文獻閱讀器：左側 PDF、右側 LLM 對話，靠「引用錨點」雙向連動（LLM 回答可點擊跳回原文高亮；選取原文可直接提問）。

## 鐵律（違反即打回）

1. **引用錨點是產品靈魂**：任何改動不得破壞 chunk 的 `page`/`bbox_list` 資訊鏈（解析 → 入庫 → RAG → SSE → 前端高亮）。改動相關程式碼必須跑引用命中測試。
2. **不引入 LangChain / LlamaIndex**：RAG 流程手寫，保持可控。
3. **LLM 供應商存取僅限 `backend/app/llm.py`、`backend/app/services/agent.py`（Pydantic AI / OpenAI）與 `backend/app/services/claude_backend.py`（Claude Agent SDK / 訂閱額度）**：不得在其他模組直接打供應商 API。OpenAI 供應商設定經 `llm._chat_config()`（settings 執行期覆蓋 > .env）；Claude 後端由 `settings_store` 的 `chat_backend` 分派（入口仍是 `services/agent.py`）。本地 embedding 推論收束於 `backend/app/local_embed.py`，僅 `llm.py` 得 import（M14；`embed_source` 分派見 `docs/02-architecture.md` D12）。給 LLM 用的工具放 `app/tools/`（複製 template_tool.py 新增）。
4. **Prompt 一律放 `backend/app/prompts/*.md`**：不得散落在程式碼字串裡。
5. **API 回應格式遵守 `docs/02-architecture.md` §5**：要改介面先改文件，並在任務卡註明 breaking change。
6. **秘密不入庫**：API key 只放 `.env`（已在 .gitignore）；`.env.example` 保持同步。
7. **範圍紀律**：只做任務卡上的事。發現卡外問題 → 寫進任務卡的「發現事項」或開新任務卡，不要順手改。

## 開發流程

- 分支：`main` 保持可跑；功能開 `feat/T-M1-03-upload-flow` 形式分支。
- Commit：`feat|fix|refactor|test|docs(scope): 描述`，一張任務卡至少一個 commit。
- 完成定義（DoD）：程式碼 + 測試 + 任務卡驗收步驟實際執行過 + `docs/03-roadmap.md` 勾選。
- 卡住升級：Haiku/Sonnet 遇到規格模糊或跨模組決策 → 把問題與選項寫進任務卡，標注 `needs-decision`，停止該卡，不要自行拍板。
- 規劃須含模型分工：每份 plan 產出必須替每張任務卡標注指派模型（Opus/Sonnet/Haiku）。安全敏感（OAuth、金鑰處理）與整合審查一律 Opus；小模型不勝任的卡直接升級指派，不硬塞。
- 審查修正授權：code review 發現的**明確 bug、重複程式碼、程式碼優化**可直接動工修正，不需經使用者批准；涉及**功能操作 SOP、使用者體驗、產品行為**等需人拍板的項目，以 needs-decision 或 proposed plan 升級給使用者決定。

## 程式碼慣例

### Backend（Python 3.11 / FastAPI）
- 格式化：`ruff format` + `ruff check --fix`；型別註記必寫，`mypy` 過。
- 分層：`routers/`（HTTP 薄層，不含業務邏輯）→ `services/`（業務邏輯）→ `db/`（資料存取）。
- 全 async；DB 用 asyncpg + SQLAlchemy 2.0 async session。
- 錯誤：自訂例外 → exception handler 統一轉 `{"error": {"code", "message"}}`；不得裸 `except:`。
- 測試：pytest + httpx AsyncClient；services 層必測，LLM 呼叫一律 mock。

### Frontend（React / TypeScript）
- 嚴格模式 TS，禁 `any`（不得已用 `unknown` + narrowing）。
- 狀態：zustand；伺服器資料一律經 `src/api/` typed client，元件內不得直接 fetch。
- 元件：function component + hooks；PDFPane 與 ChatPane 之間只透過 store 溝通（跳頁/高亮指令走 store action）。
- 樣式：CSS Modules；桌面優先，最低寬度 1280px。

### 資料庫
- Schema 變更一律寫 migration 檔（`backend/app/db/migrations/`，帶序號），不得手改線上 schema。
- JSONB 欄位（digest/citations/bbox_list）的結構定義寫在 `02-architecture.md` §4，改結構先改文件。

## 常用指令

```bash
docker compose up -d          # 全套啟動（api :8000, web :5173, db :5432）
cd backend && pytest          # 後端測試
cd backend && ruff check .    # lint
cd frontend && npm run dev    # 前端開發模式
cd frontend && npm run build  # 型別檢查 + 打包
```

## 目前狀態

- 階段：**M0–M11 已完成**，進行中 **M12 雲端備份（Google Drive，單向）**（見 `docs/03-roadmap.md`）。
- LLM 供應商：**NVIDIA NIM**（使用者已有 API key，開工時填入 `.env`；注意事項見 `docs/02-architecture.md` D5）。
- 驗收測試論文：3 篇已就位（`docs/fixtures/`），皆為 arXiv 版面，之後可再補其他版面。
