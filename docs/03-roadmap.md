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
