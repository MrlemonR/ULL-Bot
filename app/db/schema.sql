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
  health          TEXT,                 -- ok | degraded | down
  note            TEXT                  -- neden: "429", "günlük limit", "kullanıcı kapattı"
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

-- --- Faz 8: mail (IMAP) ----------------------------------------------------
-- Parola BURADA DURMAZ: `app/mail/secrets.py` onu sistem anahtarlığına
-- (libsecret) yazar, olmazsa data_dir altında 0600 bir dosyaya. Bu tabloda
-- sadece "parola nerede" bilgisi (`secret_backend`) tutulur.

CREATE TABLE IF NOT EXISTS mail_accounts (
  id             INTEGER PRIMARY KEY,
  name           TEXT,                  -- kullanıcının verdiği görünen ad
  email          TEXT NOT NULL UNIQUE,
  host           TEXT NOT NULL,
  port           INTEGER DEFAULT 993,
  username       TEXT NOT NULL,
  use_ssl        INTEGER DEFAULT 1,
  auth_type      TEXT DEFAULT 'password', -- password (uygulama parolası) | oauth (Google XOAUTH2)
  secret_backend TEXT,                  -- libsecret | file
  inbox_folder   TEXT DEFAULT 'INBOX',
  enabled        INTEGER DEFAULT 1,
  created_at     TEXT,
  last_sync_at   TEXT,
  last_error     TEXT
);

CREATE TABLE IF NOT EXISTS mail_messages (
  id              INTEGER PRIMARY KEY,
  account_id      INTEGER NOT NULL REFERENCES mail_accounts(id) ON DELETE CASCADE,
  folder          TEXT NOT NULL,
  uid             INTEGER NOT NULL,     -- IMAP UID (klasör + UIDVALIDITY içinde tekil)
  message_id      TEXT,                 -- RFC822 Message-ID
  from_name       TEXT,
  from_addr       TEXT,
  to_addrs        TEXT,                 -- JSON liste
  cc_addrs        TEXT,                 -- JSON liste
  subject         TEXT,
  date_ts         TEXT,                 -- ISO8601 UTC
  snippet         TEXT,                 -- ilk ~240 karakter, liste görünümü için
  body_text       TEXT,
  body_html       TEXT,
  attachments     TEXT,                 -- JSON: [{filename, content_type, size}]
  ics_payload     TEXT,                 -- text/calendar eki varsa ham ICS
  seen            INTEGER DEFAULT 0,
  flagged         INTEGER DEFAULT 0,
  answered        INTEGER DEFAULT 0,
  category        TEXT,                 -- bkz. app/mail/classify.py CATEGORIES
  category_source TEXT,                 -- rule | llm | user
  category_reason TEXT,
  summary         TEXT,                 -- LLM özeti; istenince üretilir, önbelleklenir
  summary_model   TEXT,
  summary_at      TEXT,
  synced_at       TEXT,
  UNIQUE(account_id, folder, uid)
);

-- Klasör başına UIDVALIDITY + en son görülen UID. UIDVALIDITY değişirse
-- sunucu UID'leri yeniden numaralandırmıştır, o klasörün önbelleği atılır.
CREATE TABLE IF NOT EXISTS mail_sync_state (
  account_id   INTEGER NOT NULL REFERENCES mail_accounts(id) ON DELETE CASCADE,
  folder       TEXT NOT NULL,
  uid_validity INTEGER,
  last_uid     INTEGER DEFAULT 0,
  synced_at    TEXT,
  PRIMARY KEY (account_id, folder)
);

-- --- Faz 8: takvim ---------------------------------------------------------
-- Uygulamanın kendi takvimi (Google/CalDAV yok — kullanıcı kararı).
-- Dışa/içe aktarım ICS ile, hatırlatma OS bildirim sistemiyle (dunst).

CREATE TABLE IF NOT EXISTS calendar_events (
  id               INTEGER PRIMARY KEY,
  uid              TEXT UNIQUE,         -- ICS UID; içe aktarımda tekilleştirme anahtarı
  title            TEXT NOT NULL,
  description      TEXT,
  location         TEXT,
  starts_at        TEXT NOT NULL,       -- ISO8601, UTC ofsetli
  ends_at          TEXT,
  all_day          INTEGER DEFAULT 0,
  attendees        TEXT,                -- JSON liste
  meeting_url      TEXT,                -- meet/zoom/teams bağlantısı
  source           TEXT,                -- manual | mail | agent | ics
  source_ref       TEXT,                -- mail kaynaklıysa mail_messages.id
  color            TEXT,
  reminder_minutes INTEGER DEFAULT 10,  -- -1 = hatırlatma yok
  reminded_at      TEXT,                -- bildirim gönderildiyse; tekrar göndermemek için
  created_at       TEXT,
  updated_at       TEXT
);

-- İndeksler en sonda: SQLite betiği sırayla çalıştırır, tablolar önce gelmeli.
-- Kota sorguları hep (sağlayıcı, zaman) üzerinden gidiyor.
CREATE INDEX IF NOT EXISTS idx_usage_provider_ts ON usage_events (provider, ts);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages (session_id, id);
-- Mail listesi hep (hesap, klasör, tarih) sıralı okunuyor; kategori filtresi ayrı.
CREATE INDEX IF NOT EXISTS idx_mail_account_folder_date
  ON mail_messages (account_id, folder, date_ts DESC);
CREATE INDEX IF NOT EXISTS idx_mail_category ON mail_messages (category, date_ts DESC);
-- Takvim sorguları hep bir tarih aralığı; hatırlatıcı döngüsü de buradan okuyor.
CREATE INDEX IF NOT EXISTS idx_calendar_starts ON calendar_events (starts_at);

-- Kullanıcının kendi özet kuralları (Faz 10).
--
-- Kullanıcı istedi: "her bir değişiklik için ayarlara mail kuralları ekle,
-- buradan mail için özet alırken kural ekleyebileyim". Kurallar
-- `SUMMARY_PROMPT`in sonuna ekleniyor, yani modele TALİMAT olarak gidiyor —
-- mail içeriği gibi "veri" değil. Bu yüzden yalnızca kullanıcı yazabiliyor.
CREATE TABLE IF NOT EXISTS mail_rules (
  id         INTEGER PRIMARY KEY,
  text       TEXT NOT NULL,
  enabled    INTEGER DEFAULT 1,
  created_at TEXT
);

-- Otomasyonlar (Faz 11). Tasarım: docs/OTOMASYON.md
CREATE TABLE IF NOT EXISTS automations (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL,
  goal        TEXT,                 -- kullanıcının sohbete yazdığı istek
  start_url   TEXT,
  allowlist   TEXT,                 -- JSON liste: izinli alan adları
  created_at  TEXT,
  last_run_at TEXT,
  last_status TEXT                  -- tamam | hata | yarida
);

-- Sol alttaki adım listesi.
--
-- İki katmanlı: `intent` kullanıcının okuyup düzenlediği cümle, `action`
-- çalıştırılan somut komut. Düzenleme intent'i değiştiriyor; action bir
-- sonraki çalıştırmada yeniden çözülüyor, böylece sayfa değişse de plan
-- bozulmuyor (docs/OTOMASYON.md §4).
CREATE TABLE IF NOT EXISTS automation_steps (
  id            INTEGER PRIMARY KEY,
  automation_id INTEGER NOT NULL REFERENCES automations(id) ON DELETE CASCADE,
  position      INTEGER NOT NULL,
  intent        TEXT NOT NULL,
  kind          TEXT DEFAULT 'islem',  -- sayfa | oku | yaz | tikla | bekle | kontrol
  action        TEXT,               -- JSON: {"type":"tikla","index":3}
  status        TEXT DEFAULT 'bekliyor',
  last_error    TEXT,
  updated_at    TEXT
);

CREATE TABLE IF NOT EXISTS automation_runs (
  id            INTEGER PRIMARY KEY,
  automation_id INTEGER NOT NULL REFERENCES automations(id) ON DELETE CASCADE,
  started_at    TEXT,
  finished_at   TEXT,
  status        TEXT,
  log           TEXT                -- JSON: adım adım ne oldu
);
