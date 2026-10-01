CREATE TABLE IF NOT EXISTS jobs (
  canonical_hash TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  company TEXT NOT NULL DEFAULT '',
  location TEXT NOT NULL DEFAULT '',
  url TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT '',
  posted_at TEXT,
  discovered_at TEXT NOT NULL,
  score REAL NOT NULL DEFAULT 0,
  remote INTEGER NOT NULL DEFAULT 0,
  state TEXT NOT NULL DEFAULT 'DISCOVERED',
  notified INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_jobs_discovered ON jobs(discovered_at);
CREATE INDEX IF NOT EXISTS idx_jobs_score ON jobs(score);
CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state);

CREATE TABLE IF NOT EXISTS evaluations (
  job_hash TEXT PRIMARY KEY,
  score REAL NOT NULL,
  eligible INTEGER NOT NULL,
  reasoning TEXT NOT NULL DEFAULT '',
  evaluated_at TEXT NOT NULL,
  FOREIGN KEY (job_hash) REFERENCES jobs(canonical_hash) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS applications (
  job_hash TEXT PRIMARY KEY,
  state TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '',
  screenshot_r2_key TEXT,
  applied_at TEXT NOT NULL,
  FOREIGN KEY (job_hash) REFERENCES jobs(canonical_hash) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS application_answers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_hash TEXT NOT NULL,
  question TEXT NOT NULL,
  answer TEXT NOT NULL DEFAULT '',
  needs_confirmation INTEGER NOT NULL DEFAULT 0,
  submitted INTEGER NOT NULL DEFAULT 0,
  UNIQUE (job_hash, question),
  FOREIGN KEY (job_hash) REFERENCES jobs(canonical_hash) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS outcomes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_hash TEXT NOT NULL,
  outcome TEXT NOT NULL,
  notes TEXT NOT NULL DEFAULT '',
  recorded_at TEXT NOT NULL,
  FOREIGN KEY (job_hash) REFERENCES jobs(canonical_hash) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_hash TEXT NOT NULL,
  from_state TEXT,
  to_state TEXT NOT NULL,
  timestamp TEXT NOT NULL,
  details TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_job ON audit_log(job_hash);

-- Discovery observability: one row per source attempt so coverage claims are
-- measured, not assumed. Also the health probe for stalled consumers.
CREATE TABLE IF NOT EXISTS source_health (
  source TEXT PRIMARY KEY,
  kind TEXT NOT NULL DEFAULT '',
  attempts INTEGER NOT NULL DEFAULT 0,
  yields INTEGER NOT NULL DEFAULT 0,
  last_raw INTEGER NOT NULL DEFAULT 0,
  last_kept INTEGER NOT NULL DEFAULT 0,
  last_error TEXT NOT NULL DEFAULT '',
  last_seen TEXT NOT NULL DEFAULT ''
);
