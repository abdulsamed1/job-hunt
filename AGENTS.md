# AGENTS.md

Autonomous job application agent. Python, Playwright, SQLite. Zero paid APIs.

## Commands

- `uv run pytest tests/ -q -p no:cacheprovider` — full suite (must stay green)
- `uv run python -m job_hunt.cli <scan|evaluate|tailor|apply|run>` — pipeline stages
- `python scripts/rezi_login.py` — Rezi OAuth (laptop with browser, human signs in)
- `uv run python scripts/linkedin_login.py` — LinkedIn session (unused: discovery is guest-only, apply is manual)
- `uv run python scripts/pull_master.py` — sync `data/cvs/Abdulsamed_Hamdy.pdf` from the Worker master-resume pointer (`--check` reports drift only, exit 1)

## LinkedIn: discovery only, apply is manual (owner decision 2026-10-21)

LinkedIn **search is on**; applying through the owner's account is off, for
good. The account was banned on 2026-10-02 by the old logged-in Easy Apply
engine, and no automated path may use the account again.

- **Discovery uses the public guest endpoint** (`jobs-guest/...`): no cookies,
  no login, no account. It cannot endanger the account because the account is
  never used. Two sources in `config/sources.yaml`: `linkedin_geo_recent`
  (geoId `92000000`, remote worldwide) and `linkedin_egypt` (geoId
  `106155005`, Egypt — on-site, hybrid and remote), same role queries,
  `f_TPR=r43200`.
- **Robots note (measured 2026-10-21)**: `linkedin.com/robots.txt` ends with
  `User-agent: *` / `Disallow: /`, so the Python registry's robots gate skips
  LinkedIn: the CI/local sweep never fetches it, and the probe records it as
  **not measured** (`flaky`/`unknown`, never `dead`). The Worker guest fetcher
  is the only LinkedIn intake. That asymmetry is the owner's accepted
  trade-off — the guest endpoint needs no account, so no account can be
  banned. If the owner wants robots honoured on the Worker too, delete the
  `linkedin` case in `worker/src/stages/discover.ts`: discovery stops, apply
  stays manual.
- **No auto-apply, three layers**: the Worker `tailor-apply` stage saves a
  LinkedIn row as `APPLICATION_STARTED` with "discovery only — apply manually"
  and never calls an ATS client; the Python `run_application_stage` filters
  LinkedIn rows out unconditionally; and
  `BrowserApplicationEngine.fill_and_submit` refuses a LinkedIn posting before
  launching a browser — no flag (`linkedin_approved`, `LINKEDIN_ENABLED`,
  discovery on/off) changes that.
- Switches gate **discovery only**: `linkedin_enabled: true` in
  `config/sources.yaml` (default when the key is absent: off) or the
  `LINKEDIN_ENABLED` env var locally; `LINKEDIN_ENABLED = "true"` in
  `worker/wrangler.toml` on the Worker.
- Cadence: LinkedIn runs on its own `6h` cadence inside the Worker deep sweep,
  not on every hourly run (be polite to the guest endpoint).
- The logged-in Easy Apply flows, `scripts/linkedin_login.py` and the session
  plumbing remain for a future explicit manual mode; nothing automated calls
  them, and the guest endpoint needs no session.
- Nothing is deleted. The `linkedin-sr` SmartRecruiters board
  (LinkedIn-as-employer) is a different source and stays as-is.
- CI: the 6-hourly LinkedIn micro sweep and `linkedin_approved` input stay
  removed from `.github/workflows/scheduled-sweep.yml`.

## Standing LinkedIn target

- Queries: `Backend`, `Full stack`, `Software Engineer`, `React`, `Node.js`,
  `TypeScript`, `JavaScript`, `Forward Deployed Engineer` (plus
  `Frontier Deployed Engineer` on the worldwide source)
- Window: `f_TPR=r43200` (past 12 hours), every run
- Geo: remote worldwide (`geoId=92000000`, `remote_only: true`) + Egypt
  (`geoId=106155005`, no server-side remote filter)
- Volume: `max_pages_per_query=2`, `target_jobs_count=60`
- Source of truth: `config/sources.yaml` entries `linkedin_geo_recent` and
  `linkedin_egypt`

## Discovery defaults (375 configured sources)
- Recency: `hours_old=24` default on every scan (`scan --hours-old 0` disables).
  Server-side where supported (Bayt/Naukri/Zip intervals, Indeed date filter;
  LinkedIn TPR always); central `filter_recent` backstops the rest.
  Dateless postings always pass — only provably-stale ones drop.
- Geography: `scan --target-geo` is the standing policy — keep remote anywhere,
  keep anything placed in Egypt (on-site, hybrid or remote), keep
  unknown-location postings; drop only a posting proven on-site outside Egypt.
  `--remote-only` stays as the strict dial. Helpers live in
  `discovery/freshness.py` (`filter_target_geo`, `is_egypt_location`) and,
  mirrored, in the Worker's `lib/freshness.ts` (`filterTargetGeoList`).

## Board coverage (375 configured; measured 2026-10-21)

Configured ≠ working. `config/sources.yaml` holds **375** entries; the live
probe (`scripts/probe_sources.py`) measured **169 live**, **175 dead** and
**31 unknown**. Treat the config as intent, never as proof — the per-board
answer lives in `data/source_probe.json`.

- **Liveness rule**: a source is live when it yields at least one **usable**
  posting **at any age** — remote anywhere, placed in Egypt (on-site, hybrid or
  remote), or unknown location. The 24h window gates the *feed*, not the
  source, and the geography gate is the shipped `--target-geo` policy, so an
  Egypt on-site board counts and is never dropped for lacking remote roles. A
  board that answers 404/410 is **dead** (the org migrated ATS — GitHub and
  DoorDash 404 on Greenhouse while `figma` returns 200, so slug derivation is
  correct); a failed attempt (timeout, 5xx, 403, unparseable body) is **flaky**
  → `status: unknown`, never a stale `status: live`. The adapters raise on a
  failed fetch, so the probe can tell the two apart. A robots.txt refusal is
  raised as `RobotsDisallowed` and reads as *not measured* (flaky) — never
  dead, because nothing was measured.
- **Live by adapter (measured 2026-10-21)**: greenhouse 70, ashby 42 (+1
  `ashby-index`), web 24, feed 12, lever 9, teamtailor 2, recruitee 2, bayt 1,
  bdjobs 1, freehire 1, naukri 1, personio 1, smartrecruiters 1, workable 1.
- **Dead by adapter**: greenhouse 60, lever 44, web 33, ashby 19,
  smartrecruiters 14, recruitee 2, teamtailor 1, feed 1, bamboohr 1.
- **Unknown/flaky by adapter**: web 24, feed 3, linkedin 2, indeed 1, lever 1.
- **Egypt-relevant boards**: WUZZUF (live), `bayt-egypt-tech` (live — the
  target-geo liveness rule counts its Egypt on-site roles), GulfTalent (403 on
  the last probes → flaky, stays in the config and is re-probed),
  `linkedin_egypt` (Worker guest only — the Python registry refuses it on
  robots, see LinkedIn above). `Bayt` (web) and `indeed-egypt-tech` are flaky.
- `status: dead` is honoured everywhere: the Worker generator excludes those
  boards from the shard, and `SourceRegistry.load_sources_file` skips them
  locally (the CI sweep stops spending budget on them). The probe itself
  re-measures dead boards (`include_dead=True`) so a recovered board is
  revived.
- **Dead — do not re-add without re-probing**: the 404 ATS boards (github,
  doordash and 100+ others), polygon.technology jobs, consensys.net/careers,
  alchemy.com/jobs, and the never-yielding boards (Toptal, Hired, Arc
  (formerly Vettery), AngelList Talent, useWeb3 Jobs, YCombinator Jobs,
  CryptoJobsDaily, Degen Crypto Jobs, Gitcoin, Stellar Jobs, Uniswap Careers,
  OpenSea Careers, RemoteOK web page, …).
- **Flaky (403 / timeout / DNS / unparseable) — re-probed, but they never
  yield**: Indeed, Glassdoor, Gun.io, Web3 Talent, JobStreet, JobsDB, Stack
  Overflow Jobs (sunset), NFT Jobs, FlexJobs (paywall), Talent.io (login),
  Bayt (web), GulfTalent, BitcoinJobs, Blockchain Job Board, Blocktribe,
  CryptoJobHunt, CryptoJobLand, CryptoJobsDB, DAO Jobs, Ethereum Jobs,
  Polygon Ecosystem Jobs, Solidity Jobs, Remote.co, NodeFlair, swissdevjobs,
  workingnomads, weworkremotely (406).
- **Feeds without job URLs** (cryptojobs.com) and **private Ashby APIs**
  (chainlink-labs, aave) are intentionally not wired: no stable job identity.
- Every new board must prove yield before its entry stays: `scan_source`
  count > 0 on the Python side, `POST /probe {"name": ...}` showing usable
  postings on the Worker. Probing lives in triage, not the product.

## Worker source coverage is generated (never hand-kept)

- `worker/scripts/gen_sources.py` emits `worker/src/sources.generated.ts` from
  `config/sources.yaml`: **150 Worker-runnable + 50 explicitly python-only**
  (web 48, naukri 1, bayt 1) + **175 dead** (excluded from both shards) = 375
  accounted for.
- `--check` mode fails on drift; `tests/test_worker_source_shard.py` enforces it
  in CI. **Never hand-edit `sources.generated.ts`** — edit `sources.yaml`.
- Worker gotchas that cost real debugging time, documented in
  `worker/README.md`: ATS list endpoints omit descriptions (must enrich),
  `upsertJob` must backfill richer text, `/run` must fan out the whole shard,
  and empty-skill-evidence must never become eligible.

## Safety rules (violations fail review)

1. **LinkedIn applies are manual, always.** Discovery is guest-only (no
   account). No automated path may apply through the owner's LinkedIn account:
   the Worker tailor-apply stage queues a LinkedIn row for the human, the
   Python application stage filters LinkedIn rows out, and the browser engine
   refuses one before building a context. `linkedin_approved=True` is not a
   bypass — the refusal is unconditional. Dry-run stays the default mode.
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

<!-- code-review-graph MCP tools -->
## MCP Tools: code-review-graph

**This project has a knowledge graph. Start with the code-review-graph
MCP tools to narrow scope, then read the source.** The graph is cheaper than scanning files and
gives you structural context (callers, dependents, test coverage) that file search cannot.

### When to use graph tools FIRST

- **Exploring code**: `semantic_search_nodes_tool` or `query_graph_tool` instead of Grep
- **Understanding impact**: `get_impact_radius_tool` instead of manually tracing imports
- **Code review**: `detect_changes_tool` + `get_review_context_tool` instead of reading entire files
- **Finding relationships**: `query_graph_tool` with callers_of/callees_of/imports_of/tests_for
- **Architecture questions**: `get_architecture_overview_tool` + `list_communities_tool`

### Verify in the source

- Narrow scope with the graph, then read the source. Do not change code from graph output alone.
- For any non-trivial change, read the implementation and the relevant tests before concluding.
- Verify the exact source when touching behavior, database logic, migrations, retries, fallbacks,
  recovery, or compatibility code.
- When the graph and the source disagree, the source wins. The graph may be stale or may not
  model that relationship.
- An empty graph result can mean "not indexed" or "not statically visible", not "does not exist".

### Key Tools

| Tool | Use when |
| ------ | ---------- |
| `detect_changes_tool` | Reviewing code changes — gives risk-scored analysis |
| `get_review_context_tool` | Need source snippets for review — token-efficient |
| `get_impact_radius_tool` | Understanding blast radius of a change |
| `get_affected_flows_tool` | Finding which execution paths are impacted |
| `query_graph_tool` | Tracing callers, callees, imports, tests, dependencies |
| `semantic_search_nodes_tool` | Finding functions/classes by name or keyword |
| `get_architecture_overview_tool` | Understanding high-level codebase structure |
| `refactor_tool` | Planning renames, finding dead code |

### Workflow

1. The graph auto-updates on file changes (via hooks).
2. Use `detect_changes_tool` for code review.
3. Use `get_affected_flows_tool` to understand impact.
4. Use `query_graph_tool` pattern="tests_for" to check coverage.
<!-- /code-review-graph MCP tools -->

## Deployment (free tiers)

- **GitHub Actions** (`.github/workflows/scheduled-sweep.yml`): daily full
  200-source 24h `--target-geo` sweep (the 175 dead boards are skipped) +
  dry-run apply (LinkedIn rows are queued for the human, see LinkedIn above;
  manual dispatch without `full` runs apply only).
  Live runs only via manual dispatch with `live`.
  Secrets via GitHub Secrets (`REZI_MCP_TOKEN`). ~840 min/mo worst case.
- **Cloudflare Worker** (`worker/`, full pipeline, not just scout): live at
  `https://jobhunt.habdulsamed777.workers.dev`. Hourly scout shard (25 sources)
  + **6-hourly deep sweep** (all 150 Worker-runnable) across RSS/JSON/ATS/tenant
  sources (LinkedIn guest included on its 6h cadence, discover-only — see
  LinkedIn above) →
  queue fan-out (1 source / 1 job per message) → deterministic
  evaluate → tailor (pdf-lib PDF → R2) → direct ATS HTTP POST applies
  (Greenhouse/Lever approved per-board; Ashby dry-run until pinned).
  D1 mirrors the SQLite state machine and holds `source_health` for measured
  coverage. Deploy: `npm install`, `npm test`, `wrangler login`, `d1 create`,
  `d1 execute --remote --file schema.sql` (**`--remote` is required**),
  queues + R2 create, `secret put` (`PROFILE_JSON`, …), `deploy`.
  Regenerate the shard first: `python worker/scripts/gen_sources.py`.
  Verify every deploy: `python scripts/verify_deploy.py --new-only`
  (fails on unreachable endpoints, safety-flag drift, or transport errors;
  quiet boards warn, they don't fail).
  Morning check: Telegram alert, or `GET /recent` → **`actionable`** (not
  `eligible`, which empties out once jobs are tailored); `GET /coverage` for
  honest source numbers.

  Feed-quality guarantees (24h window): recency window is 24 hours everywhere
  (`HOURS_OLD = 24` on the Worker; Python clamps per-source `hours_old` to 24).
  Dateless postings are stored for the record, counted as `dateless_dropped`,
  and never scored or applied. Role gate rejects leadership, BD, CRM and
  non-engineering titles (counted in `role_rejected`). A domain word
  (`accountant`, `finance`, `legal`, `medical`/`nurse`) rejects only when the
  title names **no** engineering build role (engineer, developer, devops,
  architect, programmer, sre): "Full Stack Engineer - Finance" and "AI
  Engineer, Legal & Compliance" are software jobs, "Senior Accountant" and
  "Legal Counsel" are not. People-management and revenue titles are unaffected
  ("Engineering Manager, Data Feeds", "Sales Engineer" stay rejects). Dedupe by
  role fingerprint (counted in `duplicate_suppressed`). `GET /coverage` reports
  `dated_fresh`, `proxy_fresh` (greenhouse `updated_at` proxy),
  `dateless_dropped`, `duplicate_suppressed`, `role_rejected` and
  `live_software` (distinct companies with a software-verdict posting in 24h).
  Probe a board: `uv run python scripts/probe_sources.py` (writes
  `data/source_probe.json`; `--apply` rewrites `status:` in
  `config/sources.yaml`). The probe measures liveness = the source yields at
  least one remote posting at any age (the 24h window gates the feed, not the
  source); it re-measures dead boards too, so a recovered board is revived
  automatically. Backfill existing violations (dry-run default):
  `uv run python scripts/backfill_feed_quality.py` (add `--apply` to write).
  Off Workers, always: Playwright browser, LLM-heavy stages, Rezi mirroring,
  LinkedIn submits, CAPTCHA jobs (human-queued). On Workers: fetch/parse,
  deterministic eval, pdf-lib PDFs, direct ATS POSTs (approved per-board).
- **Notifications**: only the **outcome** events reach Telegram by default —
  `SUBMITTED`/`DRY_RUN`/`APPLICATION_ATTEMPT`/`FAILED`/`STAGE_FAILED`. The
  stage chatter (`SEEKING`/`FILTERED`/`ELIGIBLE`/`TAILORED`) is suppressed in
  both runtimes; the owner asked for the application story, not the
  pipeline's progress report. Override with `TELEGRAM_EVENTS`
  (comma-separated event names; absent or empty = the default set) — the
  Worker reads the `TELEGRAM_EVENTS` var, the local runtime the
  `TELEGRAM_EVENTS` env var, and both parse it the same way. The full event
  vocabulary is unchanged (`STAGE_FAILED` worker-only, `RETRY`/`RECOVERY`
  python-only) — a muted event is simply never sent.
  Secrets: GitHub Actions via repo secrets, Worker via `wrangler secret`;
  web-UI runs stay silent; `--no-notify` per CLI run. Worker claims the ledger
  key pre-send (at-most-once) — a muted event is still claimed, so the ledger
  stays consistent and a retry stays silent; local retries a failed send on the
  next emission. Disability gate re-runs at tailor/apply time in both runtimes.
  Telegram inbound is owner-restricted: PDF uploads via `POST /telegram/webhook`
  (secret token header) update the master resume pointer
  (`settings.master_resume_sha` in D1; `MASTER_RESUME_SHA` secret is the
  bootstrap fallback) and `/master` reports it. Everything else is ignored.
  Keep the local `data/cvs/Abdulsamed_Hamdy.pdf` in sync with
  `uv run python scripts/pull_master.py --check` before local runs.
