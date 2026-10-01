# AGENTS.md — job-hunt working contract

Autonomous job application agent. Python, Playwright, SQLite. Zero paid APIs.

## Commands

- `uv run pytest tests/ -q -p no:cacheprovider` — full suite (must stay green)
- `uv run python -m job_hunt.cli <scan|evaluate|tailor|apply|run>` — pipeline stages
- `python scripts/rezi_login.py` — Rezi OAuth (laptop with browser, human signs in)
- `uv run python scripts/linkedin_login.py` — LinkedIn session (laptop, human logs in)

## Standing LinkedIn target (do not change without user approval)

- Queries: `backend`, `fullstack`, `software`
- Window: `f_TPR=r43200` (past 12 hours), every run
- Geo: `geoId=92000000`, `distance=25` (worldwide in practice)
- Low volume: `max_pages_per_query=2`, `target_jobs_count=60`
- Source of truth: `config/sources.yaml` entry `linkedin_geo_recent`

## Safety rules (violations fail review)

1. **LinkedIn never auto-submits.** `require_linkedin_approval=True` default;
   live runs need explicit `linkedin_approved=True`. Dry-run is the default mode.
2. **Wrong-job guards stay on.** Target-job verification + Easy Apply modal
   company check in `src/job_hunt/automation/browser.py` must pass before any
   submit path. Never weaken to first-match clicking.
3. **No hidden text, no keyword stuffing.** ATS keyword lines must be visible
   and contain only verified profile skills. Genuine gaps stay missing.
4. **Secrets never in git.** Tokens via `REZI_MCP_TOKEN` env / `wrangler secret`;
   `config/rezi.json`, `.rezi_token`, `data/linkedin_state.json` are gitignored.
   Never print tokens, cookies, or passwords in logs, output, or chat.
5. **Passwords are never handled here.** All logins (Rezi, LinkedIn) happen in
   the user's own browser via the `scripts/*_login.py` helpers.
6. **Rezi writes are create-only.** Never pass the master resume ID; store the
   returned per-job ID in `rezi_resume_id`; read-back verify every write.
7. **Every code change needs tests.** Red → green; full suite green before commit.
