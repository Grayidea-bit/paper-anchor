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

### M0 — 專案骨架（全部 [sonnet]，約 1 個工作段落）
- [ ] backend：FastAPI 骨架 + config.py + /healthz
- [ ] db：docker compose 起 Postgres+pgvector，schema.sql + migration 機制
- [ ] frontend：Vite+React+TS 骨架，雙欄空版面
- [ ] docker-compose.yaml 三服務一鍵啟動
- **DoD**：`docker compose up` 後前後端互通、DB migration 可跑。

### M1 — 上傳與閱讀（引用地基）
- [ ] [sonnet] POST/GET documents + 檔案儲存 + 狀態機
- [ ] [opus] ingest pipeline：PyMuPDF 解析 → 結構化 chunking（含 page/bbox）→ embedding 入庫
- [ ] [sonnet] 前端上傳流程 + 解析進度輪詢
- [ ] [opus] PDFPane：PDF.js 渲染 + 「跳到指定 page+bbox 並高亮」API（先做假資料驗證）
- [ ] [haiku] DELETE/列表 endpoint + 前端文獻列表
- **DoD**：上傳論文 → 左欄可讀；用假引用資料能正確跳頁高亮（D1 地基驗證，過不了就停下重新設計）。

### M2 — 對話與導讀
- [ ] [opus] rag.py：檢索 + prompt 組裝 + `[C12]` 引用協定
- [ ] [sonnet] SSE 串流 endpoint + 前端 SSE client + 訊息渲染
- [ ] [sonnet] digest.py 自動導讀（含 map-reduce）+ 導讀卡 UI
- [ ] [haiku] conversations/messages CRUD + 歷史載入
- **DoD**：驗收指標 1、3、4（見 01-requirements.md §6）。

### M3 — 引用連動與選取提問（產品靈魂）
- [ ] [opus] 引用端到端：LLM 標記 → 後端結構化 → 前端可點擊 → 跳頁高亮
- [ ] [sonnet] 選取文字浮動選單（解釋/翻譯/質疑/自由提問）→ 帶 selection 提問
- [ ] [sonnet] 導讀卡要點 → 點擊跳轉原文
- [ ] [haiku] 整合測試：引用命中率測試集（`docs/fixtures/` 3 篇，每篇 5 問）
- **DoD**：驗收指標 2 + 引用點擊全數命中。

### M4 — 打磨與交付
- [ ] [sonnet] 錯誤處理總盤點（掃描版 PDF、解析失敗、LLM 超時、SSE 斷線重連）
- [ ] [haiku] token 用量統計顯示、README、部署文件
- [ ] [opus] 全案 code review + 效能檢查（30s 解析目標）
- **DoD**：01-requirements.md §6 四項驗收全過；新環境照 README 可從零啟動。

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
