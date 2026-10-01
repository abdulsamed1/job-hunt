CREATE TABLE IF NOT EXISTS jobs (
  canonical_hash TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  company TEXT NOT NULL DEFAULT '',
  location TEXT NOT NULL DEFAULT '',
  url TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT '',
  posted_at TEXT,
  discovered_at TEXT NOT NULL,
  score INTEGER NOT NULL DEFAULT 0,
  remote INTEGER NOT NULL DEFAULT 0,
  notified INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_jobs_discovered ON jobs(discovered_at);
CREATE INDEX IF NOT EXISTS idx_jobs_score ON jobs(score);
