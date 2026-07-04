# Contributing

感謝你的興趣！Thanks for your interest in contributing.

## 開發環境 / Dev setup

見 [README](README.md) 的快速開始。需要一把免費的 [NVIDIA NIM](https://build.nvidia.com/) API key（或任何 OpenAI 相容供應商）。

## 開工前 / Before you start

1. 讀 [CLAUDE.md](CLAUDE.md) —— 專案鐵律（引用錨點資訊鏈不可破壞、LLM 只走 `llm.py`、prompt 集中在 `prompts/`）。
2. 架構決策在 [docs/02-architecture.md](docs/02-architecture.md)；要改介面先開 issue 討論。

## PR 檢查 / PR checklist

```bash
docker compose exec api pytest
docker compose exec api ruff check .
docker compose exec web npx tsc -b
```

動到 RAG / prompt / chunking 的 PR，請附上引用命中率回歸結果：

```bash
docker compose exec api python -m scripts.eval_citations
```

Commit 格式：`feat|fix|refactor|test|docs(scope): 描述`。
