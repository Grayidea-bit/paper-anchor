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

### 用 Claude 訂閱額度

如果你有 Claude Pro/Max/Team/Enterprise 訂閱，可以讓 chat 走方案額度而非計費 API key：

1. 在本機執行 `claude setup-token`（來自 [Claude Code](https://docs.claude.com/en/docs/claude-code)），複製它印出的一年效期 token。
2. 到設定頁「Chat LLM」區塊切換為 **Claude 訂閱**，把 token 貼上。

連上後，chat 會改由 Claude Agent SDK 驅動、吃你訂閱方案內含的額度；串流、引用跳轉、工具呼叫的行為與 OpenAI 相容後端完全一致。（`setup-token` 是 Anthropic 官方唯一背書的登入方式——本專案不內建任何逆向的 OAuth 端點。）

此功能僅涵蓋 chat；embedding 仍需 NIM 或其他 OpenAI 相容供應商的 key（見上方 `EMBED_*`），訂閱額度不含 embedding。

提醒：訂閱額度依 Anthropic 消費者條款僅限個人自用，請勿用這個後端承接共用或正式環境流量。

### 雲端備份（Google Drive）

在設定頁連接自己的 Google Drive，可以手動或定時把 PDF、標註、翻譯表與對話資料**單向備份**到雲端（非同步同步）。備份是增量式的：遠端已存在的 PDF 不會重複上傳。

#### Google OAuth client 申請步驟

1. 進入 [Google Cloud Console](https://console.cloud.google.com/)
2. 建立新專案 → 進入該專案
3. **APIs & Services** → **Enable APIs and Services** → 搜尋並啟用 **Google Drive API**
4. **OAuth consent screen** → User Type 選 **External** → **Publishing status 務必設為「In production」**（重要：停留在 Testing 模式下 refresh token 會每 7 天自動過期，導致備份連線反覆中斷）
5. **Credentials** → **Create Credentials** → **OAuth client ID** → Application type 選 **Desktop app** → 建立
6. 複製 **Client ID** 與 **Client Secret**
7. 在本程式設定頁「備份」區塊，貼上 client ID 與 secret，儲存後點「連接 Google Drive」——會跳轉授權頁；授權後自動回傳

#### 注意事項

- **本機開發與本機部署**：授權流程預期瀏覽器與伺服器在同一台機器（redirect URL 為 `http://localhost:8000/...`），開箱即用。
- **遠端主機部署**：若伺服器部署在遠端，在本機執行 `ssh -L 8000:遠端IP:8000 -L 5173:遠端IP:5173 user@遠端主機`，保持 SSH 連線開啟，再用瀏覽器訪問 `http://localhost:5173` 進行授權。
- **單向備份語意**：本機刪除文獻**不會**刪除 Google Drive 上的備份檔案；備份會記錄該次備份時點的全量狀態。

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
