# AGENTS.md

Autonomous job application agent. Python, Playwright, SQLite. Zero paid APIs.

## Commands

- `uv run pytest tests/ -q -p no:cacheprovider` — full suite (must stay green)
- `uv run python -m job_hunt.cli <scan|evaluate|tailor|apply|run>` — pipeline stages
- `python scripts/rezi_login.py` — Rezi OAuth (laptop with browser, human signs in)
- `uv run python scripts/linkedin_login.py` — LinkedIn session (unused while LinkedIn is disabled)
- `uv run python scripts/pull_master.py` — sync `data/cvs/Abdulsamed_Hamdy.pdf` from the Worker master-resume pointer (`--check` reports drift only, exit 1)

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

## Discovery defaults (374 configured sources)
- Recency: `hours_old=24` default on every scan (`scan --hours-old 0` disables).
  Server-side where supported (Bayt/Naukri/Zip intervals, Indeed date filter;
  LinkedIn TPR unused while disabled); central `filter_recent` backstops the rest.
  Dateless postings always pass — only provably-stale ones drop.
- Remote: `scan --remote-only` keeps remote-signalled + unknown-location
  postings, drops placed on-site ones. Helpers in `discovery/freshness.py`.

## Board coverage (374 configured; probed live 2026-10-19)

Configured ≠ working. `config/sources.yaml` holds **374** entries; the live
probe (`scripts/probe_sources.py`) measured **170 live**, **176 dead** and
**28 unknown** (27 boards that failed this attempt + LinkedIn, disabled).
Treat the config as intent, never as proof.

- **Liveness rule**: a source is live when it yields at least one **remote**
  posting **at any age**. The 24h window gates the *feed*, not the source, and
  the remote filter stays on because only remote roles are applicable to this
  product. A board that answers 404/410 is **dead** (the org migrated ATS —
  GitHub and DoorDash 404 on Greenhouse while `figma` returns 200, so slug
  derivation is correct); a failed attempt (timeout, 5xx, 403, unparseable
  body) is **flaky** → `status: unknown`, never a stale `status: live`. The
  adapters raise on a failed fetch, so the probe can tell the two apart.
- **Dead by adapter**: greenhouse 60, web 33, lever 44, ashby 19,
  smartrecruiters 14, recruitee 2, feed 1, bayt/indeed/bamboohr 1 each.
- **Flaky by adapter** (`status: unknown`, re-probed next run): web 23,
  feed 3, lever 1, linkedin 1.
- **Live by industry**: software 116, crypto 27, general 20, fintech 7.
- `status: dead` is honoured everywhere: the Worker generator excludes those
  boards from the shard, and `SourceRegistry.load_sources_file` skips them
  locally (the CI sweep stops spending budget on them). The probe itself
  re-measures dead boards (`include_dead=True`) so a recovered board is
  revived.

- **Live boards (measured 2026-10-19)** — every name below is recorded
  `live` in `data/source_probe.json`; this is a measurement, not a wish list:
  - **Feeds (12)**: arbeitnow, crypto-jobs-feed, cryptocurrencyjobs,
    cryptojobslist-feed, jobicy, jobspresso, remote3-feed, remoteok,
    remotive, Remotive, tokyodev-feed, weworkremotely-feed.
  - **Greenhouse (70)**: acquia, affirm, airbnb, alphasights, anduril,
    anthropic, box, brex, carta, checkr, chime, circleci, cloudflare,
    coalition, cockroachlabs, coinbase, consensys, cribl, customerio,
    dashlane, datacamp, datadog, dropbox, elastic, fastly, figma, fivetran,
    gitlab, glean, godaddy, gohiring, grafana, gusto, honeycomb, instacart,
    intercom, jackson-river-togetherwork, labelbox, launchdarkly, liveperson,
    lyft, metalab, mixmax, mongodb, newrelic, okta, openzeppelin, pagerduty,
    pinterest, planetscale, platformsh, reddit, ripple, robinhood, rocketchat,
    samsara, scaleai, scandit, securityscorecard, sofi, streamnative, stripe,
    sumologic, togetherai, turing, twilio, udacity, verkada, warp, wizeline.
  - **Ashby (42 + `ashby-index`)**: alan, alchemy, andela, baseten, braintrust,
    buffer, capchase, caremessage, cerebras, clerk, close, cognition,
    cursor-ai, deepgram, elevenlabs, etched, factory, langchain, lightspeed,
    linear, litmus, llamaindex, matx, midjourney, mux, percona, pika,
    poolside, posthog, primer, quora, rain-ai, recharge, resend,
    stellar-board, sunno, supabase, testgorilla, truelogic, uniswap,
    unstructured, zapier + `ashby-index` (solana-ecosystem-index).
  - **Lever (9)**: britecore, collabora, contentsquare, envato, kinsta,
    mindful, offchain-labs, renofi, spotify.
  - **Tenant boards (8)**: teamtailor 3 (saasglobal, teamtailor-career,
    teamtailor-softwarefinder-na), recruitee 2 (kodify, recruitee-make),
    smartrecruiters 1 (ubisoft), personio 1 (personio-vivid),
    workable 1 (netguru).
  - **JobSpy-pattern boards that yield (3)**: naukri (India, RSA token),
    freehire, bdjobs (Bangladesh). `bayt` and `indeed` answer 404 (dead),
    `glassdoor` answers 403 (flaky); ziprecruiter and google are not
    configured.
  - **HTML-scraped `web` boards (25, python-only)**: Aave Careers, Coinbase
    Careers, Crypto Careers, CryptoJobs, CryptoJobs.com, CryptoJobsList,
    DeFi Jobs, Dice, Eurojobs, GulfTalent, Remote 💎 Web3 Jobs, Remote3,
    RemoteLeaf, RemoteMore, Ripple Careers, Solana Ecosystem Jobs, TokyoDev,
    WUZZUF, Web3 Jobs, Web3.career, Web3internships,
    Wellfound (formerly AngelList), chainlink-web, jobberman, remotive-web.
- **Dead — do not re-add without re-probing**: the 404 ATS boards (github,
  doordash and 100+ others), polygon.technology jobs, ethgigs,
  consensys.net/careers URL, alchemy.com/jobs URL, and the clean-zero boards
  (Toptal, Hired, Arc (formerly Vettery), AngelList Talent, useWeb3 Jobs,
  YCombinator Jobs, CryptoJobsDaily, Degen Crypto Jobs, Gitcoin,
  Stellar Jobs, Uniswap Careers, OpenSea Careers, RemoteOK web page, …).
- **Flaky (403 / timeout / DNS / unparseable) — re-probed, but they never
  yield**: Indeed, Glassdoor, Gun.io, Web3 Talent, JobStreet, JobsDB,
  Stack Overflow Jobs (sunset), NFT Jobs, FlexJobs (paywall), Talent.io
  (login), Bayt, BitcoinJobs, Blockchain Job Board, Blocktribe,
  CryptoJobHunt, CryptoJobLand, CryptoJobsDB, DAO Jobs, Ethereum Jobs,
  Polygon Ecosystem Jobs, Solidity Jobs, Remote.co, NodeFlair, swissdevjobs,
  workingnomads, weworkremotely (406), prominentedge (lever timeout).
- **Feeds without job URLs** (cryptojobs.com) and **private Ashby APIs**
  (chainlink-labs, aave) are intentionally not wired: no stable job identity.
- Every new board must prove yield before its entry stays: `scan_source`
  count > 0 on the Python side, `POST /probe {"name": ...}` showing
  `remote_fresh > 0` on the Worker. Probing lives in triage, not the product.

## Worker source coverage is generated (never hand-kept)

- `worker/scripts/gen_sources.py` emits `worker/src/sources.generated.ts` from
  `config/sources.yaml`: **149 Worker-runnable + 49 explicitly python-only**
  (web 48, naukri 1) + **176 dead** (excluded from both shards) = 374
  accounted for.
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
  198-source 24h-remote sweep (the 176 dead boards are skipped) + dry-run
  apply (LinkedIn sources filtered out,
  see LinkedIn status above; manual dispatch without `full` runs apply only).
  Live runs only via manual dispatch with `live`.
  Secrets via GitHub Secrets (`REZI_MCP_TOKEN`). ~840 min/mo worst case.
- **Cloudflare Worker** (`worker/`, full pipeline, not just scout): live at
  `https://jobhunt.habdulsamed777.workers.dev`. Hourly scout shard (25 sources)
  + daily deep sweep (all 149 Worker-runnable) across RSS/JSON/ATS/tenant
  sources (LinkedIn-guest
  filtered out while disabled, see LinkedIn status above) →
  queue fan-out (1 source / 1 job per message, ≤50 subrequests) → deterministic
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
  non-engineering titles (counted in `role_rejected`). Dedupe by role
  fingerprint (counted in `duplicate_suppressed`). `GET /coverage` reports
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
- **Notifications**: both runtimes emit the same lifecycle events to Telegram
  (`SEEKING/FILTERED/ELIGIBLE/TAILORED/APPLICATION_ATTEMPT/SUBMITTED/DRY_RUN/
  FAILED`; `STAGE_FAILED` worker-only, `RETRY`/`RECOVERY` python-only).
  Secrets: GitHub Actions via repo secrets, Worker via `wrangler secret`;
  web-UI runs stay silent; `--no-notify` per CLI run. Worker claims the ledger
  key pre-send (at-most-once); local retries a failed send on the next
  emission. Disability gate re-runs at tailor/apply time in both runtimes.
  Telegram inbound is owner-restricted: PDF uploads via `POST /telegram/webhook`
  (secret token header) update the master resume pointer
  (`settings.master_resume_sha` in D1; `MASTER_RESUME_SHA` secret is the
  bootstrap fallback) and `/master` reports it. Everything else is ignored.
  Keep the local `data/cvs/Abdulsamed_Hamdy.pdf` in sync with
  `uv run python scripts/pull_master.py --check` before local runs.
