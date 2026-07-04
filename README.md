# AI 文獻導讀（ai-paper-reader）

讓使用者與 LLM 在「同一篇文獻」上互動的雙欄閱讀器：

- 左欄 PDF 原樣渲染，右欄對話。
- LLM 回答附**可點擊引用**，跳回原文並高亮 — 回答可驗證。
- 選取原文任一段落即可**就地提問**（解釋 / 翻譯 / 質疑）。
- 上傳後自動產生**結構化導讀**（研究問題 / 方法 / 發現 / 貢獻 / 限制），每項可跳轉原文。

## 技術棧

FastAPI + PostgreSQL(pgvector) + PyMuPDF｜React + TypeScript + PDF.js｜NVIDIA NIM（OpenAI 相容，可抽換）｜Docker Compose

## 文件導覽

| 文件 | 內容 |
|---|---|
| [docs/01-requirements.md](docs/01-requirements.md) | 需求分析：Persona、功能清單、驗收指標 |
| [docs/02-architecture.md](docs/02-architecture.md) | 架構：選型理由、引用錨點設計、資料模型、API 規格 |
| [docs/03-roadmap.md](docs/03-roadmap.md) | 路線圖：M0–M4 里程碑、Opus/Sonnet/Haiku 任務分工 |
| [CLAUDE.md](CLAUDE.md) | 開發守則（AI 開發者必讀） |

## 啟動（M0 完成後生效）

```bash
cp .env.example .env   # 填入 LLM API key
docker compose up -d   # web :5173 / api :8000 / db :5432
```

## 狀態

規劃完成（2026-07-04），下一步：M0 專案骨架。
