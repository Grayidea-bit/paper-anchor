# 開發路線圖與任務分工 — AI 文獻導讀

> 角色：PM / Tech Lead
> 狀態：v1.0（2026-07-04）

## 模型分工原則

| 模型 | 適合任務 | 本專案指派 |
|---|---|---|
| **Opus** | 跨模組設計、難題攻堅、程式碼審查 | D1 引用錨點端到端設計、RAG pipeline、PDF 高亮層、每個里程碑結束的整合審查 |
| **Sonnet** | 主力功能開發（規格明確的中型任務） | Router/Service 實作、前端元件、SSE 串流、資料層 |
| **Haiku** | 規格完全明確的小任務 | CRUD endpoint、typed API client、測試撰寫、文件、lint 修整 |

**指派規則**：任務卡上標注 `[opus]` `[sonnet]` `[haiku]`。若 Haiku/Sonnet 執行中發現需要跨模組決策，停下來把問題寫進任務卡升級給上層模型，不要自行拍板架構。

## 里程碑

### M0 — 專案骨架（全部 [sonnet]，約 1 個工作段落）✅ 2026-07-04
- [x] backend：FastAPI 骨架 + config.py + /healthz
- [x] db：docker compose 起 Postgres+pgvector，schema.sql + migration 機制
- [x] frontend：Vite+React+TS 骨架，雙欄空版面
- [x] docker-compose.yaml 三服務一鍵啟動
- **DoD**：`docker compose up` 後前後端互通、DB migration 可跑。✅ 驗證：healthz db:true、6 張表建立、web :5173 proxy 通、pytest/ruff 過。

### M1 — 上傳與閱讀（引用地基）✅ 2026-07-04
- [x] [sonnet] POST/GET documents + 檔案儲存 + 狀態機
- [x] [opus] ingest pipeline：PyMuPDF 解析 → 結構化 chunking（含 page/bbox）→ embedding 入庫
- [x] [sonnet] 前端上傳流程 + 解析進度輪詢
- [x] [opus] PDFPane：PDF.js 渲染 + 「跳到指定 page+bbox 並高亮」API
- [x] [haiku] DELETE/列表 endpoint + 前端文獻列表
- **DoD**：✅ 三篇 fixture 全數 ready（9p/4s、14p/7s，遠低於 30s 目標）；瀏覽器實測隨機 chunk 跳頁高亮兩次，含跨欄 9 區塊案例，定位全部準確。
- 發現事項：標題抽取需排除 arXiv 直排戳記（已修）；雙行標題僅取首行（可接受，記錄於 fixtures README）。

### M2 — 對話與導讀 ✅ 2026-07-04
- [x] [opus] rag.py：檢索 + prompt 組裝 + `[C12]` 引用協定
- [x] [sonnet] SSE 串流 endpoint + 前端 SSE client + 訊息渲染
- [x] [sonnet] digest.py 自動導讀（長文獻頭尾取樣）+ 導讀卡 UI
- [x] [haiku] conversations/messages CRUD + 歷史載入
- **DoD**：✅ 指標 1（導讀卡五節全有引用、chips 可點跳轉；導讀改為 ready 後非同步補上，不擋閱讀）；指標 3（實測「訓練成本/雲端供應商」問題，回答「文獻中未提及」並附最接近段落）；指標 4（curl 建立的對話重開瀏覽器後完整重現）。瀏覽器實測：提問→串流回答→點引用 chip→跳第 7 頁高亮 Table 8，數字與回答吻合。
- 發現事項（留給 M4）：(a) ~~markdown 未渲染~~ 已修（react-markdown + 引用 chip 保留）；(b) 整份 PDF 一次渲染 9+ 張 canvas，CDP 截圖變慢、低階機器可能吃力 → 考慮頁面虛擬化；(c) ThinkFilter 與引用解析已有單元測試守護。
- 使用者回饋修正（2026-07-04）：markdown 渲染 ✅；回答語言中英切換 ✅（per-request `language` 參數 + UI 選單，localStorage 記憶；導讀重生成也支援 `?language=`）。
- UI/UX 重設計（2026-07-04，使用者要求）：書齋編輯風（紙/墨/氧化紅、Source Serif 4 + Noto Serif TC + IBM Plex Mono）、對話改訪談式 Q/A 版面、導讀卡期刊摘要風；i18n 升級為全系統 zh-TW/en（`src/i18n.ts`，頂欄段落式切換，介面文字+回答語言+導讀語言一體）。修正：引用跳轉的 smooth scrollIntoView 會被同幀 DOM 變更取消 → 改為對捲動容器顯式 scrollTo，並在頁面渲染完成後重新校正。
- 穩定性修正：NIM 會在 SSE stream 內回傳錯誤物件（如 `ResourceExhausted` 限流），先前被靜默吞掉導致空回答入庫——現在偵測並回報 error 事件、空回答不入庫、prompt 過濾空歷史訊息、chat max_tokens 提高到 6144（推理段+答案共用預算）。

### M3 — 引用連動與選取提問（產品靈魂）
- [x] [opus] 引用端到端：LLM 標記 → 後端結構化 → 前端可點擊 → 跳頁高亮（M2 已完成並實測）
- [x] [sonnet] 選取文字浮動選單（解釋/翻譯/質疑/自由提問）→ 帶 selection 提問
      （瀏覽器實測通過：圈選→選單→解釋→回答精準針對選取段落並附引用）
- [x] [sonnet] 導讀卡要點 → 點擊跳轉原文（M2 已完成）
- [x] [haiku] 整合測試：引用命中率測試集（`scripts/eval_citations.py`，3 篇 × 5 問）
- **DoD**：✅ 全部達成（2026-07-04）。評測 **15/15 通過**（每題皆有結構化引用、頁碼有效、錨點可高亮）。首輪 11/15 的 4 個失敗全為 NIM 限流，加入退避重試後滿分。
- 過程修正：PageCanvas cleanup 時 `renderTask.cancel()`；移除 React StrictMode（與 pdf.js canvas 衝突）；llm.py 三出口加限流重試。
- 重要經驗：pdf.js render 依賴 requestAnimationFrame，**分頁不可見時 rAF 停發、渲染暫停**（分頁恢復可見會自動續跑）——自動化測試時務必保持視窗前景，一度被誤判為「渲染卡死」。

### M4 — 打磨與交付 ✅ 2026-07-04
- [x] [opus] PDF 頁面虛擬化：可視範圍 ±1600px 才渲染；引用跳轉目標頁強制渲染＋捲動校正（實測 14 頁文獻初開僅渲染 3 頁，跳最後一頁正常）
- [x] [sonnet] 錯誤處理：對話失敗「重試」按鈕、上傳超限清檔、限流退避（M3 已加）、掃描版明確報錯（M1 已有）
- [x] [haiku] token 用量統計（`GET /api/usage` + 文獻庫底部顯示）、README 重寫（開源導向，含疑難排解）
- [x] [opus] 全案 code review：4 項當場修正、待辦與安全清單見 `docs/reviews/M4.md`
- **DoD**：四項 MVP 驗收全過（M2/M3 已驗）；README 快速開始流程完整。⚠️ 首 token 3s 目標對推理模型不適用，已記錄於 README。

### M5 — 專案分類與多層級問答 ✅ 2026-07-04
- [x] migration 002：projects 表、documents.project_id、conversations 三態 scope（互斥 CHECK，既有資料零遷移）
- [x] 檢索 scope 化：`similar_chunks_scoped`（SQL 層硬隔離 + window function 防單篇洗版，多篇 top-12/每篇≤4）
- [x] 引用協定改全域 chunk id `[C{id}]`：跨文獻不撞號；citations 加 label/document_id/document_title；舊訊息以 chunk_index fallback 零遷移相容
- [x] API：projects CRUD、文獻指派（PATCH）、project/library conversations、send_message 依 scope 分支（selection 僅限 document）
- [x] 前端：chatContext 與 viewer 解耦、pendingJump 跨文獻跳轉、Library 專案分組（建立/改名/刪除/指派/問答入口）、跨文獻 chip 樣式
- **驗證**：pytest 21 passed；`eval_citations --scope project` 隔離鐵證 3/3（範圍外文獻零引用）；瀏覽器實測：專案問答比較兩篇論文 → 點跨文獻 chip → viewer 切換 + 第 11 頁高亮 + 對話不中斷。
- 修正：長距離跳轉改即時捲動（smooth 會被進行中的 canvas 渲染取消）。

### M6 — 「石墨編輯」視覺重設計 ✅ 2026-07-04（Claude Design 交接實作）
- [x] 設計來源：claude.ai/design 專案（方向 2a），交接包 README + 畫布存於使用者端；規格移植至 `docs/design-brief.md` 流程
- [x] Design tokens 全面改版：深色 Graphite 為預設 + 淺色靜物編輯對應，`data-theme` 切換（header ☀/☾，localStorage 記憶）
- [x] 字型：Newsreader 取代 Source Serif 4；陶紅 accent 取代氧化紅；琥珀高亮語意保留
- [x] 文獻庫改側欄式（240px 導航樹：全部文獻/專案色點/未分類/新專案/token 統計 + 主區列表）
- [x] 閱讀視圖：桌面底浮起紙頁、底部頁碼膠囊（捲動即時更新）、右緣迷你高亮指示條、高亮 ring+脈衝+「§ 錨點」標籤、浮動選單新樣式（菱形箭頭）
- [x] 引用 chip 改版：同文獻＝上標式 `p.N`（陶紅點線底）、跨文獻＝填色「標題 · p.N」、解析中＝淡色
- [x] **思考中卡片**（新功能）：後端把 reasoning_content 以 SSE `reasoning` 事件串流（不入庫）；前端顯示計時「思考中 · Ns」+ 串流推理摘要（固定高度防跳動）；回答開始後收合為「已思考 Ns ▸」可展開
- 驗證：pytest 21、ruff、tsc 全綠；瀏覽器實測深/淺兩主題、側欄導航、chip 跳轉（頁碼膠囊同步 p.8/9、錨點標籤、指示條）全部符合設計稿。

### M7 — Pydantic AI Agent 環境 + 設定 Modal + PDF 縮放 ✅ 2026-07-05
- [x] 煙霧測試先行：NIM + deepseek-v4-flash + Pydantic AI tools 實測可用（工具觸發 3/3、ThinkingPart 原生輸出、usage 含工具輪；失敗案例全為 NIM 容量限流）——框架採用決策以實據拍板
- [x] `services/agent.py`：Pydantic AI 對話管線（事件映射、4xx 剝工具降級、限流退避、UsageLimits(5)）；llm.py 移除舊 chat_stream；測試抓到並修正 PartStartEvent 首 token 遺漏
- [x] `app/tools/`：複製即註冊（ENABLED/TOOLS 協定、schema 自動生成）、template_tool.py 模板、keyword_search 範例（ToolReturn.metadata 引用鏈）
- [x] 設定 Modal：RPM（60 秒窗）+ tokens 用量、chat LLM 執行期切換（settings 表 + 白名單 + key 遮罩）、附加 system prompt、語言/主題選項陣列驅動、工具唯讀清單；修瀏覽器 autofill 誤填
- [x] PDF 獨立縮放：50–200% 步進 25、localStorage、等比捲動校正、沿用虛擬化；實測 canvas/高亮同步 1.25×、右欄零影響
- [x] healthz 改讀 _chat_config（設定切模型後顯示正確）；docs D7 + CLAUDE.md 鐵律 3 措辭更新
- 驗證：pytest 39 passed（+18 新增，含 TestModel/FunctionModel 原生測試）、ruff、tsc 全綠；工具 SSE 事件鏈 curl 實測（keyword_search start→done）。

### M8 — Claude Agent SDK 後端（訂閱額度）2026-07-05
- [x] 煙霧測試（比照 M7 先驗證後整合）：claude-agent-sdk 0.2.110（wheel 捆綁原生 CLI，容器免裝 Node）、options.env 注入 `CLAUDE_CODE_OAUTH_TOKEN` 生效、`include_partial_messages` 逐 token 串流（含 thinking_delta）、`@tool`+MCP 側信道、usage 欄位——全 PASS
- [x] `services/claude_backend.py`：Claude SDK 聯絡層，把 SDK 訊息/事件流映射成同一協定（token/reasoning/tool/context_chunks/usage）；`agent.stream_chat()` 依 settings `chat_backend`（`'openai'`|`'claude-sdk'`，預設 openai）分派，router/SSE/引用鏈/前端零改動
- [x] 工具橋接：`build_sdk_mcp_server()` 把 app/tools/ 同批函式轉 SDK MCP 工具，metadata chunks 走 contextvars 側信道傳遞（業務碼零改動）
- [x] 安全鎖定：`tools=[]`＋`setting_sources=[]`＋白名單 `allowed_tools`（僅 mcp__anchor__*），實測模型無任何內建工具（Bash/Read/Write 全消失）
- [x] 登入方式：官方 `claude setup-token` 貼碼（設定頁貼一年效期 token → 存 settings_store SECRET_KEYS）
- **決策**：chat 第二後端採 Claude SDK（使用者用 Pro/Max 訂閱額度），digest/embedding 續用 NIM；settings modal 加「NIM ↔ Claude 訂閱」後端切換
- **OAuth 一鍵登入：評估後放棄**。曾嘗試複刻 setup-token 的 PKCE 授權流程（逆向 CLI 2.1.191：入口 claude.com/cai/oauth/authorize、redirect platform.claude.com、scope user:inference），但瀏覽器點 Authorize 一律被前端擋「Invalid request format」（受控瀏覽器 session 與本機 CLI 登入上下文不同），且該 authorize/token 端點**從未官方公開、屬逆向不受支援**——Anthropic 已於 2026-03 要求 opencode 移除同款整合。放進開源專案有相容性與授權風險，故僅保留官方 setup-token 貼碼。
- 驗證：pytest 63 passed（含 test_claude_backend 事件映射/側信道/安全組態/token 取用、test_settings_store 新鍵）、ruff、tsc 全綠
- 記錄：docs/02-architecture.md D7「後端分派」小節；本段。

### M9 — 每對話模型選擇（兩層設定） 2026-07-05
- **目標**：把模型選擇拆兩層——設定頁只切「後端來源」（NIM/OpenAI 相容 ↔ Claude 訂閱，沿用 `chat_backend`）；模型改在**對話區下拉切換**，選的模型**寫入該對話（DB `conversations.model` 欄）、跟著該對話活動**（歷史重開能讀回）。
- **NIM/OpenAI 來源**：使用者在設定頁填**多個模型**（settings 鍵 `llm_chat_models`，字串陣列，e.g. `["deepseek-v4-flash", "deepseek-v4-pro"]`）；對話區下拉顯示這份清單；首個元素作 `llm._chat_config()` 預設。
- **Claude 訂閱來源**：**內建固定版本號清單**（後端 `app/models_catalog.py` 的 `CLAUDE_MODELS = ["claude-sonnet-5", "claude-opus-4-8", "claude-haiku-4-5"]`，前端靜態對應 `{value, label}`），全部已用訂閱 token 探測可用；不支援使用者自訂，Anthropic 版本更新時後端程式碼變動。
- **實現**
  - Migration `004_conversation_model.sql`：加 `conversations.model TEXT` 欄，允許 NULL（既有對話回落預設）。
  - 新 PATCH `/api/conversations/{id}` 請求體 `{model: string}`，寫入該對話 DB 欄。
  - `send_message` 讀 `conversations.model` 欄，若不為空傳入 `agent.stream_chat(model=...)`；否則用 llm 預設鏈決定。
  - llm 預設鏈（digest/healthz 用同一邏輯）：`llm_chat_models[0]`（若非空） > `llm_chat_model`（env）> 供應商硬預設。
- **分派與校驗**：`agent.stream_chat` 開頭增加單一 choke point `_resolve_model(backend, model, user_models_list)`——
  - 允許清單校驗：`claude-sdk` 後端＝`CLAUDE_MODELS`；`openai` 後端＝使用者的 `llm_chat_models`（設定項，空則用 env 單一值）。
  - 若 `model is None` 或 **不在允許清單**，靜默回落該來源預設（無報錯，使用者無感；digest/healthz 等機制呼叫）。
  - 校驗後 `model` 傳入 OpenAI SDK（`OpenAIChatModel(model_override=model)`）或 Claude backend（`_build_options(model=model)`）。
- **驗收**
  - pytest 75 passed（含新的 test_model_resolution、test_conversation_model_persistence）、ruff、tsc 全綠
  - 實測 E2E PASS：
    - NIM 模型清單 roundtrip（設定存 3 個模型 → 新建對話下拉顯示全部 → 選第 2 個 → 對話讀回）
    - 對話 model 持久化（新對話選 `claude-opus-4-8` 發問 → 重開頁面 → 同對話下拉仍顯選中 opus）
    - Claude 指定版本送問得 4 個可跳轉引用（提問「訓練資料」回答精確度）
    - 無 model 新對話、settings 不含 llm_chat_models 時，無報錯且能以預設模型對話
  - digest 與 healthz 用 llm 預設鏈，對話記錄鎖定為 conversations.model

### M10 — 使用者標註（底線／底色／註解 + 筆記面板 + AI 讀取標註）2026-07-05
- **目標**：PDF 工具列模式化〔游標｜底線｜底色｜縮放〕+ 4 色盤；游標選單加「加註解」；右欄「對話/筆記」分頁籤，筆記可跳回圈選範圍；`list_annotations` 工具讓 AI 總結使用者畫線重點。設計全文見計畫檔（annotations 表複用 page+bbox_list 座標語言，與引用鏈同構、零接觸）。
- [x] [haiku] T-AN-01 資料層+API：migration 005_annotations、repo CRUD+scoped、routers/annotations.py、docs §4/§5/D8、tests/test_annotations.py
- [x] [haiku] T-AN-02 前端 API+store：client.ts Annotation types+4 函式、annotationStore.ts、i18n 字串（依賴 01 介面）
- [x] [opus] T-AN-03 座標換算+渲染層：selectionBBox.ts（DOM rects → PDF pt、清洗合併）、PageCanvas data-scale、三種標註樣式、與 citation 高亮共存（依賴 02）
- [x] [sonnet] T-AN-04 工具列+建立流程：模式切換 UI、色盤、underline/highlight 直接建立、「加註解」popover（依賴 03）
- [x] [sonnet] T-AN-05 筆記面板：RightPane 分頁籤（ChatPane keep-mounted）、NotesPane 列表/跳轉/刪除/編輯（依賴 02，可與 03/04 並行）
- [x] [sonnet] T-AN-06 list_annotations 工具：app/tools/ + repo scoped 查詢 + tests mock（依賴 01）
- [x] [opus] T-AN-07 整合審查+回歸：全鏈驗收、eval_citations 回歸、雙主題目測、本節勾選
- **DoD**：✅ 全部達成（2026-07-05）。pytest **99 passed**（annotations CRUD/422/級聯 + list_annotations scope 隔離）、ruff/format 全綠、`npm run build`（容器內 tsc+vite）過；migration 005 套用、annotations 表結構符合規格。**eval_citations 15/15 不退化**（claude-sdk 後端實跑，鐵律 1 守住）。API roundtrip 四端點全對（201×3/422/list/PATCH/204×3/404）；`/api/tools` 含 list_annotations。瀏覽器 E2E 全 PASS：底線→底色換色→游標加註解三筆建立、筆記籤三種 icon+色點+note 顯示、點跳轉命中、刪除、**重載原位重現**、**zoom 150% 底色框精準對齊**。
- 發現事項/當場修正：T-AN-04 加註解在座標換算失敗時 `bbox_list:[]` 會觸發後端 422 並被 store 靜默吞掉，使用者誤以為已存 → **已修**（bbox 無效時「加註解」鈕 disabled + popover 儲存禁用 + 提示文字）。其餘遺留（CRUD 失敗無 toast、空 bbox note 跳轉無框）記於 `docs/reviews/M10.md`，不擋 v1。
- **試玩回饋追加（2026-07-05）**：
- [x] [sonnet] T-AN-08 repo SQL 可移植化：`chunks_by_ids`/`chunks_by_indexes` 的 `ANY()` → `IN` + `bindparam(expanding=True)`（引用鏈上兩函式原零測試覆蓋）；新增 tests/test_repo.py（10 測試，真 SQLite DB）；拆 test_list_annotations 的 12 處 test double 改跑真 SQL；requirements 補 aiosqlite（conftest 既有隱性依賴）。驗證：pytest **109 passed**、真 Postgres 容器內 parity 實測（含 -1/未知 index 靜默跳過）。
- [x] [sonnet] T-AN-09 點擊標註操作選單（使用者需求：像 Word 直接在原文上操作，不必繞筆記面板）：collapsed click 對該頁標註 bbox 命中測試（÷scale 回 pt、2pt 容差、重疊取最晚建立），彈出〔4 色換色｜問 AI｜編輯備註｜刪除〕；標註層維持 pointer-events:none、選字流程零影響；「問 AI」走 requestSelectionAsk 帶原文+備註入對話。瀏覽器實測 8 項全過（換色即時、問 AI 帶文、備註保存、刪除同步、空白不彈、選字不變）。
- [x] [sonnet] T-AN-10 標註互動改版（使用者試玩後拍板：桌面版「先畫再選」較順手）：移除底線/底色模式與工具列色盤（工具列只留縮放），圈選選單整合〔底線｜背景｜選色｜加註解〕lucide 圖示組＋原 AI 文字動作；ColorDots 共用元件（平常單顆當前色、點開展開 4 色、選色不關選單），annotMenu 同步圖示化；標註「問 AI」改只帶選取區原文。新依賴 lucide-react。瀏覽器實測 8 項全過。

### M11 — 互動打磨 + 翻譯表 2026-07-05（使用者試玩回饋驅動）
- [x] [sonnet] T-AN-11 對話串流中斷：streamMessage 支援 AbortSignal（abort 靜默返回）；串流中送出鈕變停止方塊；部分文字保留+「已中斷」標記、不觸發重試 UI；後端取消路徑確認不存半答案（零改動）。瀏覽器實測：中斷/續問/重整三情境全過。
- [x] [sonnet] T-AN-12 圈選自動附掛提問 chip：圈選即掛 chip（chunkId 非同步補填不阻塞選單）、`SelectionAsk.auto` 旗標防搶焦點；選單移除「提問…」鈕。實測：chip 即時、焦點在 BODY、覆蓋語意/手動移除正常。
- [x] [sonnet] T-TR-01 翻譯表後端：migration 006 glossary_entries（同 annotations 錨定語言）、CRUD+retranslate 端點、`translation_target_lang` 設定鍵（預設繁體中文）、prompts/translate_term.md、services/glossary.py 走既有 llm.chat()（鐵律 3/4）；LLM 失敗降級存空譯文不 500。真 NIM 實測翻譯/換語言/重翻全過；順修 conftest async_client 的 SessionLocal patch 從未生效 bug。
- [x] [haiku] T-TR-02 前端資料層：client GlossaryEntry+4 函式、glossaryStore（creating 旗標）、SettingsModal「翻譯目標語言」segmented（繁中/English/日本語）、i18n 8 鍵。
- [x] [sonnet] T-TR-03 翻譯表 UI：SelMenu「加入翻譯表」（lucide Languages，>200 字 disabled）、右欄第三分頁〔對話|筆記|翻譯表(M)〕keep-mounted、GlossaryPane（術語|譯文兩欄、失敗重試、點列跳回原文、刪除、空狀態）。真 NIM 全流程瀏覽器實測通過。
- [x] [sonnet] T-TR-04 條目從對話翻譯萃取：migration 007 加 notes 欄；POST 可帶 source_text（詳細翻譯全文）→ glossary_extract.md prompt 萃取「譯文/註解」兩行（解析失敗降級整段當譯文）；無 source_text 走原直翻。真 LLM 實測萃取正確、舊條目向後相容。
- [x] [sonnet] T-TR-05 翻譯後加入（取代選單獨立 🌐 鈕，使用者拍板整合）：翻譯 preset 帶 anchor（page/bbox）→ 候選綁定該則回答（session 內）→ 回答下方「＋加入翻譯表」帶全文萃取 → ✓已加入；中斷回答不出鈕；GlossaryPane 顯示 notes。真 LLM 全流程實測。
- [x] 翻譯目標語言改自由輸入（datalist 建議 + placeholder；任意字串直接進 prompt，空值後端回落預設）。
- [x] [haiku] T-TR-06 glossary POST 支援前端直接提供 translation/notes（三層優先序，直存路徑零 LLM 呼叫）
- [x] [sonnet] T-TR-07 加入翻譯表改前端抽取（回答第一行剝 markdown 當譯文、全文存 notes、瞬間完成）+ GlossaryPane 條目懸浮視窗（markdown 完整內容/跳到原文/Esc 關閉）
- [x] [opus 主持] /code-review 全分支：8 finder 角度 37 候選 → 驗證後 7 成立 4 駁回；已修 4（store 切文獻 stale-response 競態、router 雙 session TOCTOU、ANNOT_COLORS 重複、死鍵 selAsk/cursor）。遺留 3（低優先）：PDFPane 雙 popover 樣板可抽共用、settings 前端→後端方向無 schema 守護（建議 SettingsUpdate 加 extra='forbid'）、glossary 萃取降級未截斷（現已是次要 fallback 路徑）。
- **DoD**：✅ pytest **143 passed**、ruff/format 全綠、npm run build 過；migrations 006/007 套用 dev DB。
- 發現事項/修正：~~settings 疑似要重啟 api 才生效~~ → 根因是 **router SettingsUpdate 漏 `translation_target_lang` 欄位**（Pydantic 靜默丟棄未知欄位，PUT 200 但未持久化）——已修＋守護測試（白名單鍵必須有對應 router 欄位，防同類再發）；E2E 驗證 roundtrip 與英文翻譯生效。另 T-TR-01 順修 conftest `async_client` 的 SessionLocal patch 從未生效之既有 bug。

### M12 — 雲端備份（Google Drive，單向）
- **目標**：把 PDF、標註、翻譯表、對話等本機資料**單向備份**到使用者自己的 Google Drive（非雙向同步）。設定頁「立即備份」按鈕 + 可設定的定時備份；匯出格式版本化（`format_version: 1`），預留未來 import/還原。**技術方案（使用者拍板）**：不用 rclone，直接打 Google Drive REST API（httpx，零新依賴）；rclone 記為已驗證 fallback（見 D10）。設計全文見計畫檔與 `docs/02-architecture.md` D10。
- **供應商邊界**：Drive 存取收束在 `services/gdrive.py`（OAuth loopback + REST 4 函式窄介面）；狀態全走 settings 表，**無 DB migration**。
- [x] [opus] T-BK-00 **文件先行**（鐵律 5）：02-architecture §5 加 5 條 backup 端點、新增 D10 節（rclone vs Drive API 取捨、匯出格式 v1、OAuth 設計、刪除語意、settings 新鍵）、03-roadmap 開 M12 各卡、**CLAUDE.md 加規劃須含模型分工規則**。依賴：—
- [x] [opus] T-BK-01 `services/gdrive.py`：OAuth loopback（state + PKCE）+ Drive REST client（4 函式、resumable upload、429/5xx 退避重試）+ settings 新鍵（store SECRET_KEYS + SettingsUpdate 同步）+ httpx MockTransport 測試。安全敏感，不下放。依賴：T-BK-00
- [x] [sonnet] T-BK-02 `services/backup.py` 匯出：`repo.dump_table_rows`（白名單表、排除 embedding、datetime→isoformat）+ JSON dumps + manifest v1 + staging 暫存；snapshot 測試（斷言 secrets 不在 settings.json、embedding 不在任何 dump）。依賴：T-BK-00
- [x] [sonnet] T-BK-03 編排 orchestration + `routers/backup.py` 五端點 + `asyncio.Lock` 併發防護（409 backup_running）+ `backup_last_run` 持久化 + 增量比對（遠端已存在 PDF 跳過）+ 失敗不上傳 manifest；測試。依賴：T-BK-01、T-BK-02
- [x] [sonnet] T-BK-04 lifespan 常駐排程 task（60s tick、`backup_interval_hours` + 持久化 `backup_last_run` 判斷間隔、shutdown cancel、`--reload` 不重跑）；**不引入 APScheduler**；fake clock 測試。依賴：T-BK-03
  - 發現事項：節流以「上次執行時間」計（不分成敗）——若只認成功時間，失敗後會每 tick 狂重試；失敗後等滿一個 interval 才自動重試，手動觸發不受限（backup_scheduler.py 檔頭有註記）。
- [x] [sonnet] T-BK-05 前端：`client.ts`（`BackupStatus` type + 4 函式）+ `backupStore`（狀態 + 動作 + running 時每 2s 輪詢）+ `SettingsModal` 備份區塊（client_id/secret 遮罩輸入 → 連接 Google Drive → 立即備份 + 進度 + 上次結果 + 間隔設定）+ i18n（zh-TW/en 各約 10 鍵）。依賴：T-BK-00（介面定案後可與 T-BK-03 並行）
- [x] [haiku] T-BK-06 README：Google OAuth client 申請步驟（Desktop app、`drive.file` scope、**須設 In production**——Testing 模式 refresh token 7 天過期）+ 遠端部署 SSH port-forward 註記；roadmap 勾選。依賴：T-BK-01～T-BK-04
- [ ] [opus] T-BK-07 整合審查 + 真帳號 E2E（連接→立即備份→增量→重啟不重跑→斷網失敗遠端保完整→pytest/ruff/npm build 全綠）+ 引用鏈回歸（eval_citations 不退化，鐵律 1）。依賴：全部
  - 進度：code review 完成（無高嚴重度；6 項發現已修：D10 原子性措辭限縮、串流上傳重試測試、disconnect 清 access token 快取、_pending FIFO 逐出、callback 一律回 HTML、前端間隔輸入 clamp 8760）；pytest 220 passed / ruff / npm build 全綠；live 端點煙霧測試符合 §5。**待辦：真 Google 帳號 E2E 六步**（需部署者自己的 OAuth client，無法由開發端代跑）。
- **DoD**：pytest + ruff + `npm run build` 全綠；真 Google 帳號 E2E 六步全過（見計畫檔驗收）；`eval_citations` 不退化；D10 規格與實作一致。

### M13 — 雲端匯入還原 + 設定頁重構
- **目標**：M12 單向備份實測後的回饋迭代。補上反向的**從 Google Drive 匯入還原**（restore）——把遠端 manifest 指向的一次完整備份**合併**回本機 DB（不刪本地、id 全部 remap、可比時間戳新者勝、無從比較本地優先），新文獻下載 PDF 後**逐篇同步重跑 ingest** 重建引用鏈（dump 有 digest 則跳過重生成）；缺 PDF 整篇跳過、單篇失敗續跑、重跑即修復（冪等收斂）。restore 與 backup 共用同一把鎖互斥、`settings.json` 一律不還原。同時**設定頁重構**為左側分類、右側內容的系統設定式版面（六分類 + dirty 圓點），並補強備份 UX（連接自動先存憑證、進度條 + 階段中文）。合併規則與冪等保證全文見 `docs/02-architecture.md` D11；設計全文見計畫檔。
- [x] [opus] T-RS-00 **文件先行**（鐵律 5）：02-architecture §5 加 `POST /api/backup/restore` + status 擴充（operation／last_restore／restore progress phase）、新增 D11 節（合併規則逐表全文、citations 只 remap document_id、chunk_id 一律 NULL、新文獻流程、缺 PDF 跳過、失敗續跑＋重跑修復冪等、settings 不還原、format_version 檢查、共用鎖互斥、restore_last_run 與 summary 結構）、D10 settings 鍵表補 `restore_last_run`、本節 M13 各卡。合併規則是跨模組資料語意，寫錯全下游返工。依賴：—
- [x] [opus] T-RS-01 後端還原服務：`gdrive.download_file` + `repo` restore inserts（顯式時間戳）+ `ingest_document` 加 `run_digest` 參數 + `backup.py` 鎖 helper 抽取（`try_begin`/`set_progress`）+ `services/restore.py` 合併引擎與 `run_restore` 編排 + `routers/backup.py` 的 `POST /restore` 端點 + `restore_last_run` settings 鍵；`tests/test_restore.py` 十項（空庫全還原、重疊冪等、新舊比對、FK null、citations remap、ingest 觸發/失敗續跑、409 互斥、failed 修復、守護測試、download_file MockTransport）。資料寫入正確性風險最高，不下放。依賴：T-RS-00
- [x] [sonnet] T-UX-01 設定頁左右分欄重構：`SettingsModal` 拆殼（overlay/header/左 nav/右 panel/footer 全域儲存鈕）+ `sections/` 六子元件（Usage/Llm/Prompt/Backup/Appearance/Tools）+ nav dirty 圓點 + 固定尺寸（880×min(680,86vh)）；備份分頁 UX：連接鈕自動先 PUT 僅憑證兩鍵再開授權窗 + 進度條（current/total 填色 + 「{操作}·{階段} n/m」+ 階段 i18n）；npm build + 瀏覽器實測。依賴：T-RS-00（僅 phase 命名對齊；與 T-RS-01 完全並行）
- [x] [sonnet] T-RS-02 前端匯入 UI：`client.ts`（`BackupStatus` 加 operation/last_restore + `restoreBackup()`）+ `backupStore`（`runRestore`）+ BackupSection 匯入小節（「從雲端匯入」二次確認 dialog 三句 → POST restore → 同一條進度條 → 完成顯示摘要、`ingest_failed` 警告色列篇名、citation chip 對 `document_id: null` 降級不可跳）+ i18n 約 24 鍵。依賴：T-RS-00 + T-UX-01
- [x] [opus] T-RS-03 整合審查 + 驗證：pytest/ruff/`npm run build` 全綠 + **引用鏈回歸**（鐵律 1，動了 `ingest_document` 簽名，eval_citations 不退化）+ chrome computer-use 實測 UI（六分類切換不跳動、dirty 圓點、填憑證直接連接應先 PUT 再 auth/start、備份進度條、匯入確認框→三階段進度→摘要渲染）+ 真帳號還原 E2E（與使用者協作：現庫備份→清空/新 DB→匯入→文獻/標註/對話重現、chip 跳轉正常、digest 未重生成、第二次匯入摘要全 0 冪等、本地較新標註不被蓋）+ 本節其餘卡勾選。依賴：全部
  - 發現事項：**真 Postgres E2E 抓到兩個 SQLite 測試遮蓋的 bug 並已修**——(1) asyncpg TIMESTAMPTZ 拒收 ISO 字串（repo 加 `_coerce_ts`，restore_* 全函式收 `str | datetime`）；(2) 簽章/newer-wins 比對「dump 字串 vs 本地 datetime」恆不等 → 冪等破裂、備份一律覆蓋本地（`_parse_dt` 加 datetime 直通，三種本地形態收斂 aware UTC）。審查另補：digest citations 也套 `_remap_citations`（二段式 fixup）、runRestore 後無條件 startPolling 消輪詢競態。測試 +6 共 241 passed。
  - E2E 執行紀錄：同庫還原（真 Postgres + 真 Drive）第二次匯入摘要全 0（冪等 ✓）、失敗中斷 transaction 完整回滾無殘留 ✓、UI 三階段進度條/確認框/摘要渲染 ✓、六分類切換/dirty 圓點 ✓。**未跑**：全新 DB 完整還原（需清空現庫，留使用者自選時機）；eval_citations（NIM key 未設、跑 Claude 後端會耗訂閱額度——ingest 預設路徑零改動已由審查與全套測試確認，風險低）。
- **DoD**：pytest（既有不退化 + test_restore 全過）+ ruff + `npm run build` 全綠；`eval_citations` 不退化；真帳號還原 E2E 全過；D11 規格與實作一致。

### M15 — 地基強化 / Foundation Hardening（進行中，2026-07-09 起）

- **目標**：系統仍在開發期，M12/M13 的備份還原偏「蓋房子」，先回頭鞏固地基。三路 Opus 全系統審查（資料層地基／服務層／前端）結論——**地基整體穩健**（SQL 注入面乾淨、秘密遮罩完整、引用鏈完好、XSS 無可乘之機、備份原子性論證成立），但有**三個高嚴重度缺陷**與一批「隨庫成長會惡化」的中等問題須修：
  1. **文獻黑洞 Ingest black hole**：ingest 中途程序被殺（重啟/OOM/`--reload`）→ 文獻永久卡 `parsing`/`embedding`，無重試入口；restore 修復只認 `failed` 救不了它；`insert_chunks` 不冪等使半殘狀態無法重跑。
  2. **備份 schema 漂移無守護 Backup column-drift unguarded**：`repo._DUMP_TABLE_COLUMNS` 白名單 vs 實際 schema 無測試比對——未來 migration 加欄位，備份靜默漏、還原永遠救不回；核心 Postgres 語意（TIMESTAMPTZ/JSONB/vector/CHECK）零整合測試覆蓋，SQLite 替身已漏過一次 datetime bug（M13）。
  3. **前端連接卡死 Connect deadlock**：OAuth 進行中關閉設定視窗 → `backupStore.loading` 永遠 true（module 級單例 + interval 被清），重開後「連接」永久 disabled。
- 計畫全文：`docs/plans/M15-foundation-and-M14-deferred.md`（上半部）。
- 並行性：T-FD-01/03/05/07 互不重疊可與 T-FD-02 並行；T-FD-04 獨立；T-FD-06 等 02。

**任務卡（含模型分工）**

- [x] [opus] T-FD-00 **文件先行**（鐵律 5）：§5 加 `POST /api/documents/{id}/reingest`（202／404／409）；02-architecture 補「單 process 部署假設」與「預設信任網段」小節、D4 補啟動 reconciliation 與 ingest 冪等、§4 的「刻意未建 ANN 索引」註記更新（僅 document scope 成立、library/project 為全庫精確掃描、chunk 破 ~2 萬應建 HNSW/ivfflat）；roadmap 開 M15 各卡。依賴：—
- [ ] [sonnet] T-FD-01 **ingest 冪等與自癒**：`ingest_document` 開頭無條件 `delete_chunks`（廉價換冪等）；lifespan 啟動 reconciliation（`parsing/embedding` 殘態 → `failed` + error_msg）；新端點 `POST /api/documents/{id}/reingest`（409 若在跑）+ 前端 failed 文獻「重新解析」按鈕；restore 修復範圍擴及 transient 狀態。測試：崩潰殘態重置、重跑不撞 UNIQUE、reingest 端點。依賴：T-FD-00
- [ ] [opus] T-FD-02 **Postgres 整合測試層 + 漂移守護**：薄 Postgres 測試層（用 compose 的 db 或 testcontainers，跑真 migration，消滅 4 份手刻 DDL 副本）；覆蓋 `information_schema` vs `_DUMP_TABLE_COLUMNS` 欄位守護（新欄位必須顯式決定備份或忽略）、`similar_chunks_scoped`（vector `<=>`）、`total_token_usage`（JSONB）、backup dump→restore 往返、scope CHECK、TIMESTAMPTZ；標記為獨立 pytest marker（無 Postgres 時 skip）。依賴：T-FD-00
- [ ] [sonnet] T-FD-03 **前端正確性批次**：connect loading 卡死（`stopPolling` 重置 loading）；mid-stream 錯誤後 retry 產生重複提問（剝除失敗組再重送）；SSE 壞 frame 殺整條流（`JSON.parse` 包 try/catch 略過）；backup/restore 間 error 殘留清除。依賴：—
- [ ] [opus] T-FD-04 **安全批次**（安全敏感，不下放）：compose 埠綁定改 `127.0.0.1`（db 拿掉對外埠）+ db 強密碼；無 body 的 state-changing 端點強制 `application/json`（堵跨站 form POST 觸發 restore/disconnect）；lifespan 偵測多 worker 即警告（鎖與快取為 per-process）；README 部署假設註記。依賴：T-FD-00
- [ ] [sonnet] T-FD-05 **前端串流/記憶體效能**：訊息抽 `React.memo` 子元件（token 串流不再重算全列表 markdown）；捲動改「在底部附近才自動跟隨」+ 串流中 `auto` 行為；PDF canvas 離開可視範圍回收（保留佔位高度）+ 換文獻 `key={documentId}` 重建；scroll handler rAF 節流。依賴：—
- [ ] [sonnet] T-FD-06 **後端寫入效能**：`insert_chunks` 改單條多列 INSERT、`update_chunk_embeddings` 改 executemany/VALUES JOIN（消 N+1 round-trip）；restore 的 Drive 下載移出 DB session（先下載後開 session，比照 backup 慣例）；migration 補 `annotations.chunk_id`/`glossary_entries.chunk_id` 兩個 FK 索引。依賴：T-FD-02（用其測試層驗證）
- [ ] [sonnet] T-FD-07 **工具 schema 對齊**：`tools/_input_schema` 解析 docstring Args 段補齊 per-param description/required/預設值，消除 Claude 後端工具品質劣化；兩後端 schema 一致性測試。依賴：—
- [ ] [haiku] T-FD-08 **記帳清理批次**：死 i18n 鍵刪除；aria-label 入 i18n；prompt 載入 assert 佔位符存在；`parse_citations` 正則放寬對齊文件（或改文件）；`similar_chunks_scoped` 單篇分支補 `status='ready'`；digest `_select_chunks` O(n²) 改 set；`APP_VERSION` 單一來源；`.env.example` 同步檢查。依賴：T-FD-01～07 後
- [ ] [opus] T-FD-99 **整合驗證**：pytest（SQLite + 新 Postgres 層）/ruff/npm build 全綠；瀏覽器實測（connect 卡死重現路徑修復、串流長回答捲動、failed 文獻重新解析、100 頁文獻記憶體觀測）；引用鏈回歸（動了 ingest/insert_chunks，跑 eval_citations 或以 Postgres 層等效覆蓋）；roadmap 勾選。依賴：全部

**明確不做（審查建議但擱置）Deferred**：

- **HNSW/ivfflat 向量索引**：門檻未到（chunk 總數未破 ~2 萬），記為門檻卡，屆時再評估（見 02-architecture §4 註記）。
- **`repo.py` 拆檔**：可讀性尚可，不為拆而拆。
- **usage 端點快取**：現況成本可接受。
- **Library `prompt()` 改 popover**：UX 小優化，非地基問題。
- **Claude 後端歷史保真度**：SDK 固有限制（無法完整還原多輪工具歷史），以程式碼註解記錄，不強行繞。

- **DoD**：pytest（既有 SQLite 全套不退化 + 新 Postgres marker 測試層全過）+ `ruff check` + `docker compose exec web npm run build` 全綠；黑洞重現測試（ingest 中 `restart api` → 啟動後變 `failed` 可一鍵重新解析成功）；漂移守護自證（暫加假欄位 → Postgres 守護測試必 fail）；前端連接中關 modal → 重開 → 連接鈕可用；安全（區網另一台裝置連 5432/8000 應失敗或明確記錄僅本機）；引用鏈 eval_citations 不退化（或 Postgres 層等效覆蓋）。

## 任務卡格式（放在 docs/tasks/，一任務一檔）

```markdown
# T-M1-03 前端上傳流程
指派: sonnet
依賴: T-M1-01
規格: <輸入/輸出/UI 行為，明確到不需再問>
驗收: <可執行的檢查步驟>
禁區: <不可動的檔案/不可改的介面>
```

## 進度紀錄

每完成一張任務卡，在本檔案勾選 checkbox 並 commit。里程碑結束時由 Opus 做整合審查，審查意見寫入 `docs/reviews/M{n}.md`。
