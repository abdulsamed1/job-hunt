# AGENTS.md

Autonomous job application agent. Python, Playwright, SQLite. Zero paid APIs.

## Commands

- `uv run pytest tests/ -q -p no:cacheprovider` — full suite (must stay green)
- `uv run python -m job_hunt.cli <scan|evaluate|tailor|apply|run>` — pipeline stages
- `python scripts/rezi_login.py` — Rezi OAuth (laptop with browser, human signs in)
- `uv run python scripts/linkedin_login.py` — LinkedIn session (unused while LinkedIn is disabled)

## LinkedIn status: DISABLED (account ban, 2026-10-02)

LinkedIn is off in both local and Cloudflare. Do not re-enable without the
user's explicit approval.

- Local switch: `LINKEDIN_ENABLED` env var or top-level `linkedin_enabled` in
  `config/sources.yaml`. Default is **off**; set env/config to `true` to
  re-enable. Gating lives in `src/job_hunt/settings.py`; entry points that
  respect it: discovery (`orchestrator.run_discovery_stage`), evaluate,
  tailor, application stage, and `BrowserApplicationEngine.fill_and_submit`
  (hard block, no browser launch).
- Worker switch: `LINKEDIN_ENABLED` var in `worker/wrangler.toml`
  (currently `"false"`). `activeSources()` in `worker/src/index.ts` filters
  `kind === "linkedin"` out of every shard; the `linkedin` apply path was
  already unreachable (human-queue only).
- Nothing is deleted: adapter, Easy Apply engine, `scripts/linkedin_login.py`,
  worker guest adapter all remain. The `linkedin-sr` SmartRecruiters board
  (LinkedIn-as-employer) is a different source and stays active.
- CI: the 6-hourly LinkedIn micro sweep and `linkedin_approved` input were
  removed from `.github/workflows/scheduled-sweep.yml`.

## Standing LinkedIn target (disabled; kept for reference)

- Queries: `backend`, `fullstack`, `software`
- Window: `f_TPR=r43200` (past 12 hours), every run
- Geo: `geoId=92000000`, `distance=25` (worldwide in practice)
- Low volume: `max_pages_per_query=2`, `target_jobs_count=60`
- Source of truth: `config/sources.yaml` entry `linkedin_geo_recent`

## Discovery defaults (all 325 sources)
- Recency: `hours_old=24` default on every scan (`scan --hours-old 0` disables).
  Server-side where supported (Bayt/Naukri/Zip intervals, Indeed date filter;
  LinkedIn TPR unused while disabled); central `filter_recent` backstops the rest.
  Dateless postings always pass — only provably-stale ones drop.
- Remote: `scan --remote-only` keeps remote-signalled + unknown-location
  postings, drops placed on-site ones. Helpers in `discovery/freshness.py`.

## Board coverage (325 configured; probed live 2026-10-02)

Configured ≠ working. `config/sources.yaml` holds **325** entries, but a live
probe of all 213 ATS boards showed only **81 return any jobs**; 132 org slugs
are dead (greenhouse 48/106, ashby 30/47, lever 2/45, smartrecruiters 1/15).
GitHub and DoorDash now 404 on Greenhouse while `figma` returns 200 — slug
derivation is correct, those orgs migrated ATS. Under the 24h + remote filters
**17 sources currently yield**. Treat the config as intent, never as proof.

- **RSS feeds that yield**: cryptojobslist, crypto.jobs, weworkremotely,
  tokyodev, remote3, bitcoinjobs, jobspresso (`/jobs/feed/`), RemoteOK,
  Remotive, arbeitnow, jobicy, cryptocurrencyjobs.
- **ATS boards that yield**: greenhouse (coinbase, ripple, consensys, stripe…),
  ashby (stellar, uniswap, opensea, alchemy + `ashby-index` for aggregators
  like solana jobs), lever, workable, workday, bamboohr, smartrecruiters.
- **JobSpy-pattern boards**: bayt (MENA, needs TLS impersonation via
  `discovery/tls_fetch.py`), naukri (India, RSA token), indeed incl. Egypt,
  ziprecruiter (US), glassdoor + google (opportunistic, degrade to []).
- **Dead — do not re-add without re-probing**: polygon.technology jobs,
  ethgigs, daojobs, cryptojobsdb, cryptojob.land, consensys.net/careers URL,
  alchemy.com/jobs URL, stackoverflow.com/jobs (sunset), nftjobs, flexjobs
  (paywall), talent.io (login), angel/wellfound/toptal/hired/arc/gun
  (login-gated → manual-only, never automated).
- **Feeds without job URLs** (cryptojobs.com) and **private Ashby APIs**
  (chainlink-labs, aave) are intentionally not wired: no stable job identity.
- Every new board must prove yield before its entry stays: `scan_source`
  count > 0 on the Python side, `POST /probe {"name": ...}` showing
  `remote_fresh > 0` on the Worker. Probing lives in triage, not the product.

## Worker source coverage is generated (never hand-kept)

- `worker/scripts/gen_sources.py` emits `worker/src/sources.generated.ts` from
  `config/sources.yaml`: **241 Worker-runnable + 84 explicitly python-only**
  (bayt TLS impersonation, naukri RSA, `web` HTML scraping) = 325 accounted for.
- `--check` mode fails on drift; `tests/test_worker_source_shard.py` enforces it
  in CI. **Never hand-edit `sources.generated.ts`** — edit `sources.yaml`.
- Worker gotchas that cost real debugging time, documented in
  `worker/README.md`: ATS list endpoints omit descriptions (must enrich),
  `upsertJob` must backfill richer text, `/run` must fan out the whole shard,
  and empty-skill-evidence must never become eligible.

## Safety rules (violations fail review)

1. **LinkedIn never auto-submits.** `require_linkedin_approval=True` default;
   live runs need explicit `linkedin_approved=True`. Dry-run is the default mode.
   (Moot while LinkedIn is disabled; enforced again on re-enable.)
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
  325-source 24h-remote sweep + dry-run apply (LinkedIn sources filtered out,
  see LinkedIn status above; manual dispatch without `full` runs apply only).
  Live runs only via manual dispatch with `live`.
  Secrets via GitHub Secrets (`REZI_MCP_TOKEN`). ~840 min/mo worst case.
- **Cloudflare Worker** (`worker/`, full pipeline, not just scout): live at
  `https://jobhunt.habdulsamed777.workers.dev`. Hourly scout shard (18 sources)
  + daily deep sweep (all 241) across RSS/JSON/ATS/tenant sources (LinkedIn-guest
  filtered out while disabled, see LinkedIn status above) →
  queue fan-out (1 source / 1 job per message, ≤50 subrequests) → deterministic
  evaluate → tailor (pdf-lib PDF → R2) → direct ATS HTTP POST applies
  (Greenhouse/Lever approved per-board; Ashby dry-run until pinned).
  D1 mirrors the SQLite state machine and holds `source_health` for measured
  coverage. Deploy: `npm install`, `npm test`, `wrangler login`, `d1 create`,
  `d1 execute --remote --file schema.sql` (**`--remote` is required**),
  queues + R2 create, `secret put` (`PROFILE_JSON`, …), `deploy`.
  Regenerate the shard first: `python worker/scripts/gen_sources.py`.
  Morning check: Telegram alert, or `GET /recent` → **`actionable`** (not
  `eligible`, which empties out once jobs are tailored); `GET /coverage` for
  honest source numbers.
  Off Workers, always: Playwright browser, LLM-heavy stages, Rezi mirroring,
  LinkedIn submits, CAPTCHA jobs (human-queued). On Workers: fetch/parse,
  deterministic eval, pdf-lib PDFs, direct ATS POSTs (approved per-board).
