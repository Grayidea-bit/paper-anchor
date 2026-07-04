# AI 文獻導讀 · Paper Reader

> Read papers **with** an LLM, not just ask about them. Two-pane reader: PDF on the left, cited conversation on the right — every claim clickable back to the source.

讓使用者與 LLM 在「同一篇文獻」上互動的雙欄閱讀器：

- **引用可驗證**：LLM 回答附可點擊引用 `[C12]`，點了跳回 PDF 原文並高亮——這是本專案與「把 PDF 丟給聊天機器人」的核心差異。
- **選取提問**：在 PDF 上圈選任一段文字，浮動選單一鍵「解釋／翻譯／質疑／提問」。
- **自動導讀**：上傳後自動產生結構化導讀卡（研究問題／方法／發現／貢獻／限制），每個要點可跳轉原文。
- **誠實回答**：文獻沒寫的就明說「文獻中未提及」，不編造（引用命中率評測 15/15）。
- **雙語**：介面與回答語言一鍵切換 zh-TW / en。

## 快速開始

需求：Docker（含 compose）、一把 [NVIDIA NIM](https://build.nvidia.com/) API key（免費註冊即有額度）。

```bash
git clone <this-repo> && cd ai-paper-reader
cp .env.example .env        # 填入你的 NIM API key（LLM_API_KEY 與 EMBED_API_KEY）
docker compose up -d        # web :5173 / api :8000 / db :5432
```

打開 http://localhost:5173，上傳一篇 PDF 論文即可開始。

### 換模型 / 換供應商

任何 OpenAI 相容 API 都可以用，改 `.env` 即可：

```ini
LLM_BASE_URL=...            # chat 供應商
LLM_CHAT_MODEL=...
EMBED_BASE_URL=...          # embedding 供應商（可與 chat 不同家）
EMBED_MODEL=...
EMBED_DIM=1024              # 需同步 DB schema 的 VECTOR 維度；上限 2000（pgvector 索引限制）
```

注意：NIM 的 embedding API 需要 `input_type` 參數（入庫 `passage`／查詢 `query`），`llm.py` 已處理；換供應商時留意這個差異。

## 技術棧

FastAPI + PostgreSQL(pgvector) + PyMuPDF ｜ React + TypeScript + PDF.js ｜ RAG 手寫（無 LangChain）｜ Docker Compose

## 文件

| 文件 | 內容 |
|---|---|
| [docs/01-requirements.md](docs/01-requirements.md) | 需求分析與驗收指標 |
| [docs/02-architecture.md](docs/02-architecture.md) | 架構：引用錨點設計（D1）、chunking、資料模型、API 規格 |
| [docs/03-roadmap.md](docs/03-roadmap.md) | 開發路線圖與各里程碑實錄 |
| [CLAUDE.md](CLAUDE.md) | 開發守則（含 AI 協作開發規範） |

## 開發

```bash
docker compose exec api pytest            # 後端測試
docker compose exec api ruff check .      # lint
docker compose exec web npx tsc -b        # 前端型別檢查
docker compose exec api python -m scripts.eval_citations   # 引用命中率回歸（會呼叫 LLM）
```

## 疑難排解

- **回答很久才出現第一個字**：預設模型 deepseek-v4-flash 是推理模型，思考段可能花 20–40 秒；可換 `deepseek-v4-pro` 或非推理模型。
- **LLM 呼叫失敗（ResourceExhausted）**：NIM 免費端點限流；系統會自動退避重試，仍失敗按「重試」即可。
- **切到別的分頁後 PDF 沒渲染完**：瀏覽器會暫停背景分頁的渲染（requestAnimationFrame），切回來會自動續跑，是正常行為。
- **掃描版 PDF**：無文字層，MVP 不支援 OCR，上傳會明確報錯。

## 授權

尚未決定（開源計畫中）。
