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
docker compose up -d      # web :5173 / api :8000 (both bound to 127.0.0.1)
```

Open http://localhost:5173 and upload a PDF.

Ports are bound to `127.0.0.1` (localhost only) by design — see [Deployment assumptions](#deployment-assumptions) before exposing anything to a network. The database port is not published to the host at all; the API reaches it over the Compose network.

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

### Local Embedding (no NIM required)

If you have a Claude subscription but no NVIDIA NIM key, Paper Anchor can process document embeddings with a built-in local model (BAAI/bge-m3, 1024-dim)—use your subscription for both chat and embeddings **with zero NIM dependency**.

#### How to use

1. Do **not** set `EMBED_API_KEY` in `.env` (leave it empty)
2. Open Settings, go to **Embedding source**, and select **Local model** (or it auto-activates when you switch chat to Claude subscription)
3. Upload, ask, generate digests, back up, restore — all without NIM

If you've already filled in a NIM key, you can switch the Embedding source to "Local model" at any time in Settings to force local usage. **After switching, you must rebuild the full index** (one-click button) to re-embed existing papers (mixed vector embeddings break retrieval).

#### Notes

- **First-time setup requires internet**: the model file (~2.2GB) downloads into a docker volume and is cached — rebuilding the container won't re-download it. If the download fails, you can retry later.
- **Machine requirements**: 4GB RAM recommended; typical resident use is ~1.6GB, peak ~2.5GB.
- **Inference speed**: CPU embedding a paper (5–20 pages) takes ~10–30 seconds; similar to NIM latency.
- **Backup & restore**: backup format v2 now includes vectors (base64-encoded) — if your embedding source matches at restore time, it completes in seconds with no re-embedding needed; if you switched sources, the system automatically re-embeds your papers (same as the one-click rebuild above).

### Cloud backup to Google Drive

Connect your own Google Drive in Settings to back up PDFs, annotations, glossary entries, and conversations **one-way to the cloud** (not sync). Backups are incremental: PDFs already on the remote won't re-upload.

#### Setting up Google OAuth client

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project → navigate to it
3. **APIs & Services** → **Enable APIs and Services** → search and enable **Google Drive API**
4. **OAuth consent screen** → User Type: **External** → **Publishing status must be set to "In production"** (critical: leaving it in Testing mode causes refresh tokens to expire every 7 days, breaking backups repeatedly)
5. **Credentials** → **Create Credentials** → **OAuth client ID** → Application type: **Desktop app** → create
6. Copy your **Client ID** and **Client Secret**
7. In the app Settings page, "Backup" section: paste client ID and secret, save, then click "Connect Google Drive" — you'll be redirected to authorize; the app reconnects automatically afterward

#### Notes

- **Local development & deployment**: the OAuth flow assumes browser and server are on the same machine (redirect URL is `http://localhost:8000/...`), so it works out of the box.
- **Remote host deployment**: if your server is on a remote machine, open an SSH tunnel from your local machine: `ssh -L 8000:remote-ip:8000 -L 5173:remote-ip:5173 user@remote-host`, keep it open, then visit `http://localhost:5173` in your browser to authorize.
- **One-way backup semantics**: deleting a paper locally **does not** delete its backup on Google Drive; each backup snapshot records the full state at that time.

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

## Deployment assumptions

Paper Anchor is designed for a **single user on a trusted local machine**. Two assumptions are baked into the architecture — know them before you deploy:

1. **No authentication; trust boundary is the network.** The API has no login. `docker compose` therefore binds the API (`:8000`) and web (`:5173`) ports to `127.0.0.1` only, and does **not** publish the database port to the host — the API reaches Postgres over the internal Compose network. Secrets (LLM key, Google OAuth tokens, etc.) live in the DB `settings` table and are masked by the API layer, so keeping the DB port off the host matters. State-changing `POST` endpoints without a request body carry a minimal CSRF guard: they require `Content-Type: application/json`, which a cross-site HTML form cannot set (it would trigger a CORS preflight that is not allowed). This is **not** a substitute for auth.
2. **Single worker/process.** The backup/restore/reingest mutex locks and the `settings_store` cache are per-process, in-memory state. Do not run with multiple workers (`--workers N` or multiple replicas) — concurrency guarantees and runtime settings updates would silently break. The app logs a warning at startup if `WEB_CONCURRENCY > 1`.

### Exposing it beyond localhost

Before putting this on a LAN or the internet:

- Put the API behind a **reverse proxy that adds authentication** (and TLS); do not open the raw ports. Prefer an SSH tunnel for occasional remote access (see the backup section above).
- **Change the default DB password** (`paper`/`paper` in `docker-compose.yaml`). Note: changing it on an existing deployment requires re-initializing the `pgdata` volume, since the password is baked into the volume on first init — treat it as a fresh setup.
- Keep it to a single worker.

See [docs/reviews/M4.md](docs/reviews/M4.md) for the full checklist.

### Connecting to the database directly

The DB port is not published by default. To run `psql`/a GUI client against it, either use `docker compose exec db psql -U paper paper_reader`, or uncomment the `ports` block under the `db` service in `docker-compose.yaml` (bound to `127.0.0.1:5432`). The same uncomment is required to run the Postgres test layer from the host — see [backend/tests/README.md](backend/tests/README.md).

## License

[MIT](LICENSE)
