# ULL-Bot — Personal AI Orchestrator

A personal agent that runs entirely on free-tier LLM providers and can act on
your machine (files, commands, web lookups). For the full spec see
[ORCHESTRATOR_SPEC.md](./ORCHESTRATOR_SPEC.md) (written in Turkish), and for
implementation decisions made along the way see [DECISIONS.md](./DECISIONS.md).

Current status: **Phase 2 — tools + safety** (see spec §9).

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

Everything lives in `.env` (see `.env.example`). Phase 2 knobs:

| Variable | Default | Meaning |
|---|---|---|
| `WORKSPACE_ROOT` | `~/Projects` | Agent's working directory; always allowed |
| `DRY_RUN` | `true` | Report writes instead of doing them |
| `MAX_AGENT_STEPS` | `15` | Tool-loop step limit before asking to continue |

## Tests

```bash
uv run pytest
```

Covers the safety policy (every blocked pattern, obfuscation, path traversal,
symlink escape), the tools, the agent loop (step limit, output truncation,
approval flow) and prompt injection — including a test where the model
deliberately obeys an injected `rm -rf ~` and the policy stops it.

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
- [ ] Phase 3 — multi-provider + quota tracking
- [ ] Phase 4 — router
- [ ] Phase 5 — local model
- [ ] Phase 6 — profiles (desktop/laptop)
- [ ] Phase 7 — polish
