# ULL-Bot — Personal AI Orchestrator

A personal agent that runs entirely on free-tier LLM providers and can act on
your machine (files, commands, web lookups). For the full spec see
[ORCHESTRATOR_SPEC.md](./ORCHESTRATOR_SPEC.md) (written in Turkish), and for
implementation decisions made along the way see [DECISIONS.md](./DECISIONS.md).

Current status: **Phase 1 — skeleton + single provider** (see spec §9).

## Setup

```bash
uv sync
cp .env.example .env
```

Fill in at least these in `.env`:

- `OPENROUTER_API_KEY` — get one for free at https://openrouter.ai/keys
- `LITELLM_MASTER_KEY` — any string you pick as a local password for the
  LiteLLM proxy (replace the `changeme-local-key` placeholder).

## Running it

Two processes: the LiteLLM proxy and the FastAPI app.

**1. LiteLLM proxy** (no Docker required — runs directly via `uv`):

```bash
uv run litellm --config config/litellm.desktop.yaml --port 4000
```

Or with Docker, if you prefer:

```bash
docker compose up litellm
```

**2. Orchestrator (FastAPI):**

```bash
uv run uvicorn app.main:app --reload --port 8080
```

Open http://localhost:8080 and send a message — the reply should stream in.

## Why Docker isn't required

This project targets Linux first but is planned to be ported to Windows
later. To keep the dev loop from depending on Docker Desktop, the LiteLLM
proxy also runs as a plain Python package. See DECISIONS.md →
"Cross-platform portability" for details.

## Database

SQLite is written to a platform-appropriate data directory by default
(`~/.local/share/ai-orchestrator/orchestrator.db` on Linux,
`%LOCALAPPDATA%\ai-orchestrator\orchestrator.db` on Windows). Override with
`DB_PATH` in `.env` if you want a different location.

## Roadmap

- [x] Phase 1 — skeleton + single provider
- [ ] Phase 2 — tools + safety
- [ ] Phase 3 — multi-provider + quota tracking
- [ ] Phase 4 — router
- [ ] Phase 5 — local model
- [ ] Phase 6 — profiles (desktop/laptop)
- [ ] Phase 7 — polish
