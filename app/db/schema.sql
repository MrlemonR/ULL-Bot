CREATE TABLE IF NOT EXISTS usage_events (
  id            INTEGER PRIMARY KEY,
  ts            TEXT NOT NULL,          -- ISO8601 UTC
  provider      TEXT NOT NULL,
  model         TEXT NOT NULL,
  task_type     TEXT,
  prompt_tokens INTEGER DEFAULT 0,
  completion_tokens INTEGER DEFAULT 0,
  latency_ms    INTEGER,
  status        TEXT,                   -- ok | rate_limited | error
  session_id    TEXT
);

CREATE TABLE IF NOT EXISTS provider_state (
  provider        TEXT PRIMARY KEY,
  cooldown_until  TEXT,
  last_probe_ts   TEXT,
  probe_payload   TEXT,                 -- ham JSON
  health          TEXT                  -- ok | degraded | down
);

CREATE TABLE IF NOT EXISTS sessions (
  id          TEXT PRIMARY KEY,
  created_at  TEXT,
  title       TEXT
);

CREATE TABLE IF NOT EXISTS messages (
  id          INTEGER PRIMARY KEY,
  session_id  TEXT REFERENCES sessions(id),
  role        TEXT,                     -- user | assistant | tool | system
  content     TEXT,
  tool_name   TEXT,
  model       TEXT,
  ts          TEXT
);

CREATE TABLE IF NOT EXISTS memory_notes (              -- kalıcı, oturumlar arası
  id         INTEGER PRIMARY KEY,
  key        TEXT UNIQUE,
  value      TEXT,
  updated_at TEXT
);
