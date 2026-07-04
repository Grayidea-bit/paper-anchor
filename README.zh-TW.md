# ⚓ Paper Anchor · 文獻導讀

> **每個回答，都錨定在原文上。**
> 與 LLM「在同一篇文獻上」共讀的雙欄閱讀器——AI 回答中的每個論斷都是可點擊的引用，一鍵跳回 PDF 原文並精準高亮。

[English README](README.md)

![Paper Anchor 示範——點擊引用，跳回高亮原文](docs/media/demo.gif)

## 為什麼不是又一個「跟 PDF 聊天」工具？

多數工具給你無法驗證的答案。Paper Anchor 的核心信念是：**無法追溯到原文的回答是負債，不是功能。**

- **引用錨點**——回答中每個論斷帶 `[C12]` 標記，點擊即跳到對應頁面並以 bbox 精度高亮原文區塊（不是只給頁碼）。引用完整性由自動化評測守護（內建測試集 15/15）。
- **選取提問**——在 PDF 上圈選任一段，一鍵「解釋／翻譯／質疑／提問」，選取段落與前後文強制進入檢索。
- **自動導讀**——上傳即生成結構化導讀（研究問題／方法／發現／貢獻／限制），每個要點可跳轉原文。
- **誠實設計**——文獻沒寫的就明說「文獻中未提及」，不編造。
- **自架 + 自帶金鑰（BYOK）**——PDF 不離開你的機器（除了送往你自選的 LLM 供應商）；任何 OpenAI 相容 API 皆可用；`docker compose up` 一鍵啟動。
- **雙語**——介面與回答語言一鍵切換中／英。

## 快速開始

需求：Docker（含 compose）、一把 [NVIDIA NIM](https://build.nvidia.com/) API key（免費額度）或任何 OpenAI 相容供應商的 key。

```bash
git clone https://github.com/<you>/paper-anchor && cd paper-anchor
cp .env.example .env      # 填入 LLM_API_KEY 與 EMBED_API_KEY
docker compose up -d      # web :5173 / api :8000 / db :5432
```

打開 http://localhost:5173，上傳一篇 PDF 論文。

### 換模型／換供應商

全部由 `.env` 控制：

```ini
LLM_BASE_URL=...          # 任何 OpenAI 相容 chat endpoint
LLM_CHAT_MODEL=...
EMBED_BASE_URL=...        # embedding 供應商（可與 chat 不同家）
EMBED_MODEL=...
EMBED_DIM=1024            # 需同步 DB 的 VECTOR 維度；pgvector 索引上限 2000
```

注意：NIM 的 embedding API 需要 `input_type` 參數（入庫 `passage`／查詢 `query`），`llm.py` 已處理；換供應商時留意。

## 技術棧

FastAPI · PostgreSQL + pgvector · PyMuPDF｜React · TypeScript · PDF.js｜RAG 手寫（無 LangChain）｜Docker Compose

## 文件

| 文件 | 內容 |
|---|---|
| [docs/01-requirements.md](docs/01-requirements.md) | 需求分析與驗收指標 |
| [docs/02-architecture.md](docs/02-architecture.md) | 架構：引用錨點設計、chunking、資料模型、API 規格 |
| [docs/03-roadmap.md](docs/03-roadmap.md) | 里程碑實錄 |
| [CLAUDE.md](CLAUDE.md) | 開發守則（含 AI 協作開發規範） |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 貢獻指南 |

## 開發

```bash
docker compose exec api pytest                              # 後端測試
docker compose exec api ruff check .                        # lint
docker compose exec web npx tsc -b                          # 型別檢查
docker compose exec api python -m scripts.eval_citations    # 引用命中率回歸（會呼叫 LLM）
```

## 疑難排解

- **第一個字很久才出現（20–40 秒）**——預設的 `deepseek-v4-flash` 是推理模型，思考段算在內；在意延遲可換非推理模型。
- **`ResourceExhausted` 錯誤**——NIM 免費端點限流；系統會自動退避重試，仍失敗按「重試」。
- **切到背景分頁後 PDF 停止渲染**——瀏覽器會暫停背景分頁的 requestAnimationFrame，切回即續跑，屬正常行為。
- **掃描版 PDF**——無文字層，暫不支援 OCR，上傳會明確報錯。

## 公開部署前的安全須知

這是單人、本機優先的 MVP。要對外開放前：加上認證、更換 `docker-compose.yaml` 的預設資料庫密碼、在 API 前加 TLS。完整清單見 [docs/reviews/M4.md](docs/reviews/M4.md)。

## 授權

[MIT](LICENSE)
