# ULL-Bot — Personal AI Orchestrator

A personal agent that runs entirely on free-tier LLM providers and can act on
your machine (files, commands, web lookups). For the full spec see
[ORCHESTRATOR_SPEC.md](./ORCHESTRATOR_SPEC.md) (written in Turkish), and for
implementation decisions made along the way see [DECISIONS.md](./DECISIONS.md).
Picking the work back up in a fresh session? Start at
[NEXT_PHASE.md](./NEXT_PHASE.md).

Current status: **Phase 3 — multi-provider + quota tracking** (see spec §9).

## Setup

```bash
uv sync
cp .env.example .env
```

Fill in at least these in `.env`:

- `LITELLM_MASTER_KEY` — any string you pick as a local password for the
  LiteLLM proxy (replace the `changeme-local-key` placeholder).
- At least one provider key. All three are free and none asks for a card:
  - `OPENROUTER_API_KEY` — https://openrouter.ai/keys
  - `GROQ_API_KEY` — https://console.groq.com/keys
  - `GEMINI_API_KEY` — https://aistudio.google.com/apikey

A provider with no key is dropped from the candidate list before any request is
made (there is no point collecting a 401), so starting with one key works fine —
you just don't get failover until you add a second.

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

Open http://localhost:8080 and send a message. The chat runs over a WebSocket
(`/ws/chat`) so the agent can ask for approval mid-task.

## What the agent can do

| Tool | What it does | Default risk |
|---|---|---|
| `read_file` | Read a text file, optional line range | safe |
| `list_dir` | List a directory | safe |
| `search_files` | Search file contents (ripgrep) | safe |
| `run_shell` | Run a shell command | decided per command |

Every call goes through the safety policy first, and every call is written to
the audit log.

## Providers and quota

Three free providers are wired up. `config/routing.yaml` holds the order they
are tried in; `config/litellm.<profile>.yaml` maps each abstract name to a real
model. The orchestrator never names a provider SDK — it only knows `chat-groq`,
`chat-openrouter`, `chat-gemini`.

| Provider | Model | Free limits (2026-08-16) | Where the numbers come from |
|---|---|---|---|
| Groq | `llama-3.3-70b-versatile` | 30 req/min, 12K tok/min, 1000 req/day | published docs (local counting — see below) |
| OpenRouter | `openai/gpt-oss-20b:free` | 20 req/min, 50 req/day (1000 if the account ever bought $10 of credit) | published docs + `GET /api/v1/key` probe |
| Gemini | `gemini-3.5-flash` | **not published** — per-account | local counter only |

Google no longer publishes free-tier numbers; they are per account and readable
at https://aistudio.google.com/rate-limit. The values in `config/quotas.yaml`
were read off that page rather than guessed (spec §12) — if you use a different
account, replace them. Note how tight Gemini is: 20 requests a *day* on
`3.5-flash`. `3.5-flash-lite` allows 500, which is what phase 4's task routing
is for.

**How a provider is picked.** For each candidate in the chain: no API key →
skipped; manually disabled → skipped; in cooldown after a 429 → skipped;
remaining quota below `RESERVE_RATIO` of the limit → skipped. The first survivor
wins. If nobody survives, `fallback_behaviour` in `routing.yaml` decides —
`force_first` tries the head of the chain anyway, `error` shows an error.

**On a 429.** The provider gets a cooldown (from `Retry-After`, or
`default_cooldown_seconds`), is excluded for the rest of this turn, and the
turn continues on the next provider — up to `MAX_PROVIDER_ATTEMPTS`. The switch
is not silent: the chat shows which provider it moved to, why the previous one
was dropped, and which candidates were rejected on the way.

**Counting.** Every call writes a row to `usage_events` (provider, model,
tokens, latency, status), including the failed ones — a 429 consumed a request
even though it produced no answer. Token counts come from
`stream_options.include_usage`, so streaming doesn't blind the counter. Live
probe data, when it is fresher than 15 minutes, overrides the local count; the
quota panel labels each window `canlı` or `tahmini` so you can tell which you
are looking at.

In practice only OpenRouter reports `canlı` today: LiteLLM does not forward
Groq's `x-ratelimit-*` headers to the client (tested on 1.97.0), so Groq is
counted locally too. That only loses visibility into usage of the same key by
*other* apps — hitting the real limit still produces a 429 and a cooldown.

**The panel.** The `kota` button in the header opens it: per provider, a bar per
window, the reset time, and a disable/enable button. `canlı sorgula` forces a
probe. `GET /api/quota?probe=true` returns the same data as JSON.

## Safety model

This is the part that matters — the agent has shell access on your real Arch
install. Three levels (spec §6.3):

- **safe** — runs without asking. Read-only commands from an allowlist
  (`ls`, `cat`, `grep`, `find`, `git status`, …) whose path arguments stay
  inside the workspace.
- **confirm** — you get an inline dialog with the exact command, the affected
  paths and the reason; nothing runs until you approve. This is the default for
  anything not on the read-only allowlist.
- **blocked** — never runs, no dialog, the model just gets an error.

Blocked includes: `sudo`/`su`/`doas`, package managers (`pacman`, `yay`, …),
`systemctl`, disk tools (`mkfs*`, `fdisk`, `dd`, …), `rm -rf` outside `$HOME`,
`chmod`/`chown` on system paths, `curl … | bash`, and anything that can't be
analysed statically (`$(...)`, backticks, subshells, unbalanced quotes).

Obfuscation is handled by tokenizing the command the way a shell would, so
`"su""do"` and `s\udo` are recognised as `sudo`. Non-ASCII command names
(homoglyphs like `ѕudo`) are refused.

**Paths.** `config/workspace.yaml` lists `allowed_paths` / `denied_paths` /
`denied_globs`. Every path is fully resolved (symlinks included) before being
checked, so `../../../etc/passwd` and a symlink pointing out of the workspace
are both caught. Secrets (`.env`, `*.pem`, `id_rsa*`, `~/.ssh`, browser
profiles) are denied even inside the workspace.

**Dry-run.** `DRY_RUN=true` (the default) means commands that could write are
reported instead of executed; read-only commands still run normally. Turn it
off in `.env` once you trust the setup.

**Audit log.** Every tool call, approval and refusal is appended to
`~/.local/share/ai-orchestrator/audit.log` (JSON Lines, mode 0600). That
directory is on the deny list, so the agent cannot read or edit its own trail.

**Prompt injection.** Tool output reaches the model wrapped in
`<tool_result untrusted="true">`, and the system prompt says instructions found
in tool output are never obeyed. After untrusted content enters the context,
shell calls are escalated to `confirm` even when they look harmless.

### Known gaps (see DECISIONS.md for the reasoning)

- **No trash/undo yet.** With dry-run off, an `rm` you approve really deletes.
  The automatic backup-before-delete from spec §6.3 lands with the write tools
  (`write_file`, `edit_file`, `delete_file`), which are not in phase 2.
- **`run_shell` is disabled on Windows.** The command policy is POSIX-only; a
  half-written PowerShell policy would be more dangerous than none. File tools
  work everywhere.
- **The agent runs as you**, not as a separate `aiagent` user. That's a
  deployment concern, planned with the systemd units in phase 7.

## Configuration

Everything lives in `.env` (see `.env.example`).

| Variable | Default | Meaning |
|---|---|---|
| `WORKSPACE_ROOT` | `~/Projects` | Agent's working directory; always allowed |
| `DRY_RUN` | `true` | Report writes instead of doing them |
| `MAX_AGENT_STEPS` | `15` | Tool-loop step limit before asking to continue |
| `APPROVAL_TIMEOUT_SECONDS` | `300` | Unanswered approval counts as **denied** |
| `TOOL_OUTPUT_LIMIT` | `4000` | Tool output is truncated in the middle past this |
| `SHELL_TIMEOUT_SECONDS` | `30` | Per-command timeout |
| `RESERVE_RATIO` | `0.1` | Reserve share of a quota; below it a provider is dropped before a 429 |
| `MAX_PROVIDER_ATTEMPTS` | `3` | How many providers one turn may try after 429s |

The quota numbers themselves are not env vars — they live in
`config/quotas.yaml` with a source link and a date next to each block, because
they change on the provider's schedule, not yours.

## Tests

```bash
uv run pytest
```

Covers the safety policy (every blocked pattern, obfuscation, path traversal,
symlink escape), the tools, the agent loop (step limit, output truncation,
approval flow), prompt injection — including a test where the model
deliberately obeys an injected `rm -rf ~` and the policy stops it — and the
phase 3 additions: window maths for all three reset styles, header parsing,
cooldowns, and provider selection including the 429-mid-turn switch.

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
- [x] Phase 2 — tools + safety
- [x] Phase 3 — multi-provider + quota tracking
- [ ] Phase 4 — router (task classification: trivial / reasoning / code / …)
- [ ] Phase 5 — local model
- [ ] Phase 6 — profiles (desktop/laptop)
- [ ] Phase 7 — polish
