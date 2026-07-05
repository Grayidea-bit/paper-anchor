# ⚓ Paper Anchor

> **Every answer, anchored to the source.**
> Read papers *with* an LLM — a two-pane reader where every AI claim is a clickable citation that jumps back to the exact highlighted passage in the PDF.

[繁體中文說明](README.zh-TW.md)

![Paper Anchor demo — click a citation, jump to the highlighted source](docs/media/demo.gif)

## Why another "chat with PDF" tool?

Most tools give you answers you can't verify. Paper Anchor is built around one idea: **an answer you can't trace back to the source is a liability, not a feature.**

- **Anchored citations** — every claim in an answer carries a `[C12]` chip; click it and the PDF jumps to the page and highlights the exact source blocks (bbox-level, not just page numbers). Citation integrity is guarded by an automated eval (15/15 on the bundled test set).
- **Select-to-ask** — select any passage in the PDF and instantly *explain / translate / challenge / ask* about it. The selected chunk (plus neighbors) is force-fed into retrieval.
- **Auto digest** — structured overview on upload (research question / method / findings / contributions / limitations), each point clickable back to the source.
- **Honest by design** — when the paper doesn't answer your question, it says so instead of hallucinating.
- **Self-hosted & BYOK** — your PDFs never leave your machine except to the LLM provider *you* choose. Any OpenAI-compatible API works. `docker compose up` and you're done.
- **Bilingual** — one-click zh-TW / en for both UI and answers.

## Quick start

Prereqs: Docker (with compose) and an API key from [NVIDIA NIM](https://build.nvidia.com/) (free tier available) or any OpenAI-compatible provider.

```bash
git clone https://github.com/<you>/paper-anchor && cd paper-anchor
cp .env.example .env      # fill in LLM_API_KEY and EMBED_API_KEY
docker compose up -d      # web :5173 / api :8000 / db :5432
```

Open http://localhost:5173 and upload a PDF.

### Swapping models / providers

Everything is `.env`-driven:

```ini
LLM_BASE_URL=...          # any OpenAI-compatible chat endpoint
LLM_CHAT_MODEL=...
EMBED_BASE_URL=...        # embedding provider (can differ from chat)
EMBED_MODEL=...
EMBED_DIM=1024            # must match the DB VECTOR dim; pgvector index caps at 2000
```

Note: NVIDIA NIM's embedding API requires an `input_type` parameter (`passage` for indexing, `query` for search) — handled in `llm.py`; keep this in mind when switching providers.

### Use your Claude subscription

If you have a Claude Pro/Max/Team/Enterprise subscription, you can drive chat with your plan usage instead of a metered API key:

1. On your machine, run `claude setup-token` (from [Claude Code](https://docs.claude.com/en/docs/claude-code)) and copy the one-year token it prints.
2. In the app, open Settings → Chat LLM, switch to **Claude subscription**, and paste the token.

Once connected, chat runs on the Claude Agent SDK with your subscription's included usage; citations, streaming, and tool calls behave identically to the OpenAI-compatible backend. (`setup-token` is the only authentication path Anthropic officially supports for this — the app does not embed any reverse-engineered OAuth endpoints.)

This only covers chat — embedding still needs a NIM or other OpenAI-compatible provider key (`EMBED_*` above), since subscription usage doesn't cover embeddings.

Note: subscription usage is for personal use per Anthropic's consumer terms — don't wire this backend up for shared/production traffic.

## Stack

FastAPI · PostgreSQL + pgvector · PyMuPDF | React · TypeScript · PDF.js | hand-rolled RAG (no LangChain) | Docker Compose

## Docs

| Doc | Contents |
|---|---|
| [docs/01-requirements.md](docs/01-requirements.md) | Requirements & acceptance criteria (zh-TW) |
| [docs/02-architecture.md](docs/02-architecture.md) | Architecture: citation-anchor design, chunking, data model, API spec (zh-TW) |
| [docs/03-roadmap.md](docs/03-roadmap.md) | Milestone log (zh-TW) |
| [CLAUDE.md](CLAUDE.md) | Development ground rules (incl. AI-assisted dev conventions) |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to contribute |

## Development

```bash
docker compose exec api pytest                              # backend tests
docker compose exec api ruff check .                        # lint
docker compose exec web npx tsc -b                          # typecheck
docker compose exec api python -m scripts.eval_citations    # citation-integrity regression (calls LLM)
```

## Troubleshooting

- **First token is slow (20–40s)** — the default `deepseek-v4-flash` is a reasoning model; its thinking phase counts. Switch to a non-reasoning model if latency matters more than quality.
- **`ResourceExhausted` errors** — NIM free-tier rate limiting. The app retries with backoff automatically; hit *Retry* if it still fails.
- **PDF stops rendering when the tab is in the background** — browsers pause `requestAnimationFrame` for hidden tabs; rendering resumes when the tab is visible again. Expected behavior.
- **Scanned PDFs** — no text layer, no OCR support yet; upload fails with a clear message.

## Security notes for public deployment

This is a single-user, local-first MVP. Before exposing it to the internet: add authentication, change the default DB credentials in `docker-compose.yaml`, and put the API behind TLS. See [docs/reviews/M4.md](docs/reviews/M4.md) for the full checklist.

## License

[MIT](LICENSE)
