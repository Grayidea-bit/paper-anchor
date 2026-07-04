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
