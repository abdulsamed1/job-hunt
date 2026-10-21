# jobhunt — full autonomous pipeline on Cloudflare Workers (free tier)

24/7 job capture + application across every configured source with **zero
browser**: discovery → evaluate → tailor (text CV + PDF) → direct ATS HTTP
applies, with LinkedIn disabled (account ban 2026-10-02, `LINKEDIN_ENABLED=false`)
and Easy Apply + CAPTCHA jobs held for humans.

Live: `https://jobhunt.habdulsamed777.workers.dev`

## Source coverage is generated, never hand-kept

`src/sources.generated.ts` is emitted from `config/sources.yaml` by
`scripts/gen_sources.py`, so the Worker can never silently fall behind the
Python config:

```bash
python worker/scripts/gen_sources.py          # regenerate
python worker/scripts/gen_sources.py --check  # fail on drift (used by tests)
```

| Split | Count | Why |
|---|---|---|
| Worker-runnable | **150** | greenhouse 70, ashby 42, rss 15, lever 10, linkedin 2 (guest, discovery only), teamtailor 2, recruitee 2, smartrecruiters 1, indeed 1, ashby-index 1, freehire 1, bdjobs 1, personio 1, workable 1 |
| Python-only | **50** | HTML scraping (`web` 48), Naukri (RSA handshake), Bayt (TLS impersonation) |
| Dead (excluded) | **175** | measured dead by the probe: boards that answer 404/410, or never return a usable posting |
| Total in `sources.yaml` | **375** | every source accounted for, none silently dropped |

### Measured yield (live probe, 2026-10-21)

Configured is not the same as working. The probe
(`python scripts/probe_sources.py --apply`) measures every configured source
and writes the verdict to `config/sources.yaml` and to
`data/source_probe.json`:

| Verdict | Count | Note |
|---|---|---|
| live | **169** | greenhouse 70, ashby 42 (+1 index), web 24, feed 12, lever 9, teamtailor 2, recruitee 2, bayt/bdjobs/freehire/naukri/personio/workable/smartrecruiters 1 each |
| dead | **175** | greenhouse 60, lever 44, web 33, ashby 19, smartrecruiters 14, recruitee 2, teamtailor 1, feed 1, bamboohr 1 — the boards are gone (GitHub, DoorDash and others migrated off Greenhouse: `figma` returns 200, `github` 404s — slug derivation is correct) |
| unknown | **31** | boards that failed this attempt (403, timeout, DNS, unparseable body) → flaky, re-probed next run; the two LinkedIn sources are here because robots.txt refuses automated access (`not measured`, never dead) |

**Liveness rule**: a source is live when it yields at least one **usable**
posting **at any age** — remote anywhere, placed in Egypt (on-site, hybrid or
remote), or unknown location; the 24h window gates the *feed*, not the source.
The geography gate is the shipped `--target-geo` policy (`filterTargetGeoList`),
so an Egypt on-site board counts and is never dropped for lacking remote roles.
A board that answers 404/410 is dead; a failed attempt is flaky (`status:
unknown`), never a stale `live`. The adapters raise on a failed fetch (non-2xx status,
whole-feed parse error) so the probe can tell "the board answered with
nothing" from "the board failed". `status: dead` boards are excluded from the
generated shard, so the Worker never spends requests on them.

The hourly scout shard is **25 sources** (software 11, general 6, crypto 5,
fintech 3 — at most 2 crypto orgs and 3 crypto feeds), rebuilt from the same
ledger. `GET /coverage` reports configured vs attempted vs yielding from D1 —
coverage claims are measured, never asserted. Add a board only after
`POST /probe` shows yield.

### Feed-quality guarantees

The feed is software-only and fresh. The role gate (`lib/roles.ts`, pure CPU)
classifies every title as software / reject / unknown before a posting is stored;
manager, sales, BD, CRM and non-engineering roles are stored once as
`PRE_FILTERED_OUT` and counted in `role_rejected`. A domain word (`accountant`,
`finance`, `legal`, `medical`/`nurse`) rejects only when the title names no
engineering build role, so `Full Stack Engineer - Finance` stays a software
role while `Senior Accountant` does not. Duplicate requisitions
across boards collapse by role fingerprint and are counted in
`duplicate_suppressed`. Postings older than the 24h window (`HOURS_OLD`) are
dropped before storage; dateless postings are stored for the record, counted in
`dateless_dropped`, and never scored or applied.

`GET /coverage` distinguishes dated freshness from proxy freshness:
`dated_fresh` counts postings with a real posting date inside 24h, while
`proxy_fresh` counts greenhouse-family rows whose `posted_at` came from
`updated_at` (a proxy, not a true posting date). `live_software` reports
distinct companies with at least one software-verdict posting in the last 24h.
The gate counters `role_rejected`, `duplicate_suppressed`, `dateless_dropped`
are also exposed on `GET /health`. Status field per source: `live | dead |
unknown`; `python scripts/probe_sources.py --apply` (from the repo root)
rewrites status from the measured yield (reviving recovered boards) and
records the newest posting date (`last_yield_at`) next to the probe time
(`probed_at`) in `data/source_probe.json`.

## Why this fits free tier (verified vs docs 2026-10-01)

| Constraint | Design answer |
|---|---|
| 10ms CPU/invocation | One source page / one job action per queue message; fetch wait is free, parses are small, giant feeds skipped to Actions |
| 50 subrequests/invocation | Consumer batches ≤20; each message ≈ 1 fetch + 1–2 D1 ops |
| 5 cron triggers/account | Exactly 2 used (hourly scout, daily deep sweep) |
| D1 ≤50 queries/invocation | Single statements, indexed lookups, small batches |
| No browser | ATS applies are direct HTTP POSTs (Greenhouse/Lever); Ashby live pinned per-org only; LinkedIn submits stay human |
| No Playwright CAPTCHA solving | CAPTCHA-gated jobs → BLOCKED queue + Telegram alert |

## Layout

- `src/adapters/` — RSS, JSON boards (RemoteOK/Remotive/Arbeitnow/Jobicy),
  Greenhouse/Lever/Ashby/SmartRecruiters APIs, per-tenant boards (`tenants.ts`: Teamtailor/Recruitee/Personio),
  LinkedIn guest shard (disabled via `LINKEDIN_ENABLED`)
- `src/lib/` — hash, freshness, gates, deterministic scoring, honest Q&A mapping
- `src/stages/pipeline.ts` — evaluate, tailor (pdf-lib PDF → R2), ATS apply
- `src/stages/discover.ts` — the discover stage: source guard + fetch, the role
  and duplicate gates, and the store/queue side of one source per message
  (called by the queue handler; `queueGuardAllows`, `discoverSource` and
  `linkedinEnabled` are re-exported from `index.ts`)
- `src/state.ts` — D1 state machine (mirrors the SQLite lifecycle)
- `src/sources.ts` — `SourceDef` type; `src/sources.generated.ts` — GENERATED, do not hand-edit
- `src/index.ts` — cron dispatcher (hourly shard + daily deep), queue
  consumers, HTTP API (see below)
- `scripts/gen_sources.py` — `sources.yaml` → `sources.generated.ts` generator
- `test/` — vitest suites (`npm test`), `tsconfig.json` — `npm run typecheck`

## HTTP API

| Endpoint | Purpose |
|---|---|
| `GET /health` | Job count **+ profile sanity**: `profile_skills`, `profile_languages`, `threshold`, `live_apply`, `notification_events` (ledger row count; `null` means the deployed schema predates the ledger — verify_deploy fails on it). An empty `verified_skills` silently rejects every job — check this first. |
| `GET /recent` | `actionable` (tailored / submitted / human queue), `eligible`, `latest`. **Read `actionable`** — qualifying jobs leave the `ELIGIBLE` state once tailored, so `eligible` alone always looked empty. |
| `GET /coverage` | Measured coverage from the `source_health` table, by kind. The feed-quality counters read the `jobs` table through a **window** (`discovered_at >= now - 1 day`) and a `LIMIT`: the table is cumulative history, so an unbounded read would grow with the store instead of with the feed. There is no aggregate fallback — the endpoint fails loudly rather than answering from columns the real D1 table does not have. |
| `GET /sources` | Configured shard: total, python-only, hourly subset, by kind. |
| `POST /run?deep=1` | Fan out the whole shard, one source per message, ≤50 per `sendBatch`. Omit `deep` for the hourly subset. |
| `POST /probe` | Read-only single-source yield check: `{"name":"stripe"}` → `raw` / `fresh_24h` / `remote_fresh`. Writes nothing. |
| `POST /queue` | Fetch jobs by state (`{"state":"ELIGIBLE","count":10}`). |
| `POST /telegram/webhook` | Owner-only Telegram inbound; `X-Telegram-Bot-Api-Secret-Token` must match `TELEGRAM_WEBHOOK_SECRET` (else 401). A PDF upload updates `settings.master_resume_sha` (precedence: D1 → `MASTER_RESUME_SHA` secret); `/master` reports the current sha + source; everything else is ignored. |

## Pipeline gotchas worth not rediscovering

- **Descriptions must be enriched.** ATS board *list* endpoints omit
  descriptions; scoring a title-only string rejects 100% of jobs for lack of
  evidence. `enrichDescriptions` fetches per-job content for the few jobs that
   survive the 24h + remote filters (bounded to 12 to stay under the
  50-subrequest ceiling). Greenhouse's `?content=true` on the *list* endpoint is
  rejected — it returns ~5.5 MB per board.
- **`upsertJob` backfills, it does not skip.** `ON CONFLICT DO NOTHING` left
  rows written before enrichment permanently unscoreable. It now updates when a
  description gets richer or location/date are missing, and returns `true` only
  when something materially changed — which is exactly the set whose stored
  score may be stale. A no-op upsert returns `false` and costs no queue message.
- **`/run` used to queue `SOURCES[0]`.** The full fan-out lived only in the cron
  path, so manual runs discovered a single source.
- **Empty evidence is not eligibility.** Jobs with zero skill matches score 75
  from participation points but are held back by `matched.length >= 1`. That is
  the safety property; do not loosen it to inflate numbers.
- **The feed is software-only: the role gate runs before SEEKING.** `classifyRole`
  (`lib/roles.ts`, pure CPU) buckets a title as software / reject / unknown in the
  discover loop *before* the row is stored, so a manager, sales or CRM posting is
  stored once as `PRE_FILTERED_OUT`, bumps `role_rejected` in the `counters` table
  and alerts `FILTERED` instead of `SEEKING`. **The reject families come first and
  nothing overrides them**: a revenue, CRM or non-engineering word beats any
  seniority marker (`Senior Sales Engineer` is a sales role) and so does a
  people-management word (`Senior Engineering Manager` is a manager role). The IC
  allow-list (seniority + an engineering noun) only stands in for the software
  allow-list, so `Staff Engineer, Platform` survives while `Senior Marketing
  Engineer` does not. **A domain word is not a role on its own**: `accountant`,
  `finance`, `legal` and `medical`/`nurse` reject only when the title names no
  engineering build role (engineer, developer, devops, architect, programmer,
  sre — the one `BUILD_ROLES` list the module uses). `Full Stack Engineer -
  Finance`, `AI Engineer, Legal & Compliance` and `Medical Engineer` are
  software jobs; `Senior Accountant` and `Legal Counsel` are not. People
  management and revenue are untouched: `Engineering Manager, Data Feeds` stays
  a manager role and `Sales Engineer` stays a sales role. Python applies the
  same order and the same build-role vocabulary (see
  `src/job_hunt/evaluation/engine.py`, `ROLE_REJECT_RULES`, `BUILD_ROLE_WORDS`),
  and the two runtimes must not drift: `intern`/`internship` and `full
  stack`/`fullstack`/`full-stack` are the same thing on both sides, and both
  read the shared role-gate fixtures in `tests/fixtures/role_gate_cases.json`.
  The gate reason is recorded in the
  row's `evaluations.reasoning`, **once per posting** (`instr` guard): a
  re-discovery — a dateless posting's row counts as "improved" on every sweep —
  re-counts nothing, re-notifies nothing and cannot grow the reasoning. Callers
  gate those side effects on `upsertJob`'s `outcome.recorded`, never on its
  new-or-improved return value. Counters are observability: a failed bump is
  logged, never allowed to abandon a posting.
  `scoreJob` runs the gate again as defense in depth (rows stored before it, or
  messages already in flight) and caps unclassified titles at 69, one point below
  the default threshold: unknown titles stay visible in the feed but never
  applied to. `REJECT_CRM_ROLES = "false"` admits Salesforce implementation
  roles; anything else (including an absent var) rejects them. `counters` is
  declared in `schema.sql` and created for existing databases at runtime by
  `ensureCounters(db)` once per queue batch, like `settings` and
  `notification_events`.
- **One application per requisition: the role fingerprint is a second identity.**
  `jobs.canonical_hash` is the URL alone, so the same opening arriving on two
  boards (or under two jids) used to become two applications. Every posting now
  also stores `jobs.role_fingerprint` — normalized company + role + place, with
  every all-remote spelling collapsed to one token (`lib/hash.ts`). The company
  drops its legal suffix and the title expands its abbreviations, exactly like
  Python's `dedup.py`, so `Acme, Inc.` and `Acme` are one employer and
  `Sr. Backend Engineer` and `Senior Backend Engineer` are one role — and the
  discover stage resolves the whole source's fingerprints in **one** batched
  `findKnownJobs` read (two `IN` lists, chunked at 50 bindings) before storing
  anything. The known-jobs map is keyed by namespaced identifiers (`h:` for a
  canonical hash, `f:` for a fingerprint): both are bare 8-hex digests, so a
  shared key space could resolve one kind to the other. A hit owned by a
  *different* canonical hash is stored as `DUPLICATE`
  with `duplicate of <hash>` in `evaluations.reasoning`: no SEEKING, no
  evaluation, and it never reaches the apply queue from here on. The one hole
  left is disclosed deliberately: an apply message **already in flight** when the
  posting is suppressed is not blocked — a twin that reached `ELIGIBLE` and
  queued `tailor-apply`, then lost the gate on a later sweep, can still be
  applied by that message. This stage does not guard the apply path (that is the
  apply-time re-gate's job), and closing it here is not free: relabelling an
  already-applied posting to protect a future one would destroy the history of
  the application. Three things cost debugging time here:
  - **Suppression is real, and counted on the transition.** The jobs upsert
    never touches `state`, so a posting that was still open when the gate found
    it (every row that predates the deploy) is moved with `setJobState` and its
    audit row written. `duplicate_suppressed` and the reason write ride that
    same transition, so a re-discovered twin costs neither a count nor a
    statement, and counting a suppression that was never performed is
    impossible. **Both gates demote**: the role gate moves a pre-existing row
    to `PRE_FILTERED_OUT` and the dateless drop does the same, so a posting
    that predates the gate cannot sit in `/recent`'s actionable list.
  - **Neither gate touches a posting the store has already decided.** Both act
    only on rows whose outcome is still open — `DISCOVERED` or `ELIGIBLE`
    (`GATEABLE_STATES` in `stages/discover.ts`). A row already scored out, filtered or
    suppressed has no opening left to protect and would lose the verdict its
    state records; a row already on its way to the employer (`TAILORED`,
    `SUBMITTED`, `APPLICATION_STARTED`) carries the honest history of the
    application, and a gate marker would rewrite it and its evaluation to score
    0 for a posting the owner has no remedy for. So `role_rejected` and
    `duplicate_suppressed` count postings kept out **while their outcome was
    still open**, once per posting.
  - **A suppressed twin never reopens.** A twin's fingerprint can drift (the
    board edited the title) or lose its owner; the row under the posting's own
    hash is still the authority, and it stays suppressed. The evaluate stage's
    SELECT excludes `DUPLICATE` rows, so a message queued before the gate landed
    can never be promoted into an application either.
  - **Ownership is narrow.** Only a row that can still be applied from
    (`DISCOVERED`, `ELIGIBLE`, `TAILORED`, `SUBMITTED`) owns a requisition, and
    only for 30 days. A filtered or scored-out row owns nothing — it will never
    be applied, so blocking its twin would lose the opening — and a company
    re-posting the same remote role next quarter gets a new requisition.
    Suppression removes the demoted hash from the owner set as it goes, so two
    live rows that share a fingerprint (which the backfill can create) lose one
    posting between them, not both. Known residual: two genuinely distinct
    openings at one company with the same title and place still collapse into
    one, and once a twin exists, a re-post older than the window is still
    suppressed by it.
  The column is declared in `schema.sql` and added to existing databases by
  `ensureRoleFingerprint(db)` once per queue batch, like
  `ensureSourceHealthColumns`.
- **The notification ledger claims before it sends.** `notification_events`
  (`(runtime, job_hash, event)` PK) gets the event key inserted *before* the
  Telegram call, so a queue retry that re-runs the message sees the conflict
  and skips — a Telegram outage records "failed" and never retry-storms. With
  credentials unset nothing is claimed. A **muted** event (one outside
  `TELEGRAM_EVENTS`) is claimed the same way: the ledger stays consistent and
  the retry skips, but no HTTP POST is made. The table is created at runtime
  (`ensureNotificationEvents`, once per queue batch) for databases that
  predate it; `/health` reports `null` when it is missing.
- **`settings` holds the master-resume pointer, also created at runtime.**
  `settings` (`key TEXT PRIMARY KEY`, `value TEXT NOT NULL`,
  `updated_at TEXT NOT NULL`) stores `master_resume_sha`; `schema.sql`
  declares it and `ensureSettings(db)` migrates databases that predate it,
  mirroring `ensureNotificationEvents`. The apply path resolves the pointer as
  `settings.master_resume_sha` → `MASTER_RESUME_SHA` secret, so the secret is
  only the bootstrap/fallback and a Telegram upload needs no secret rotation.
  Old R2 objects are never deleted.
- **Webhook processing is inline and idempotent (same sha = zero writes)**,
  so Telegram retries are safe. Subrequests stay modest (≈7 on the worst
  path); never log bodies — only file ids, shas, and byte counts.

## Deploy

```bash
cd worker
npm install
npm test && npm run typecheck
npx wrangler d1 create jobhunt
# paste database_id into wrangler.toml, then:
# --remote is required: without it the schema lands in local D1 only and
# production throws D1_ERROR: no such table: jobs
npx wrangler d1 execute jobhunt --remote --file schema.sql
npx wrangler r2 bucket create jobhunt-cvs
npx wrangler queues create jobhunt-discover
npx wrangler queues create jobhunt-evaluate
npx wrangler queues create jobhunt-apply
npx wrangler secret put PROFILE_JSON   # slim JSON; Workers Free caps env values at 5 KB
npx wrangler deploy
# then prove it (fails on unreachable endpoints or safety-flag drift):
python scripts/verify_deploy.py --new-only
```

Live applies stay OFF until a human flips per-board flags:

```bash
npx wrangler secret put LIVE_APPLY        # "true" only when supervised
npx wrangler secret put APPROVE_GREENHOUSE # per-board approval
npx wrangler secret put APPROVE_LEVER
```

Optional: `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` for lifecycle event
notifications. **Only the outcome events are sent by default** —
`APPLICATION_ATTEMPT` / `SUBMITTED` / `DRY_RUN` / `FAILED` / `STAGE_FAILED` —
at most once per `(runtime, job_hash, event)` via the `notification_events`
ledger. The stage events (`SEEKING` / `FILTERED` / `ELIGIBLE` / `TAILORED`)
are suppressed: the owner asked for the application story, not the pipeline's
progress report.

Override the set with the `TELEGRAM_EVENTS` var (comma-separated event names;
absent or empty = the default set). The local runtime reads the same name
from its environment, so one vocabulary serves both runtimes:

```toml
# worker/wrangler.toml [vars]
TELEGRAM_EVENTS = "APPLICATION_ATTEMPT,SUBMITTED,DRY_RUN,FAILED,STAGE_FAILED"
```

A muted event is still **claimed** in `notification_events` (the ledger claims
before it sends), so the at-most-once bookkeeping stays consistent and a
queue retry that re-runs the message skips the same way it always did — only
the HTTP POST is skipped. Unset credentials = silent no-op, nothing claimed.

Inbound master-resume updates need `TELEGRAM_WEBHOOK_SECRET` (random hex) and a
**fresh** `TELEGRAM_BOT_TOKEN` — revoke the old one in BotFather first — plus a
one-time `setWebhook` registration with the secret token:

```bash
npx wrangler secret put TELEGRAM_WEBHOOK_SECRET
npx wrangler secret put TELEGRAM_BOT_TOKEN
curl "https://api.telegram.org/bot<bot-token>/setWebhook" \
  -d "url=https://jobhunt.habdulsamed777.workers.dev/telegram/webhook" \
  -d "secret_token=<webhook-secret>" \
  -d "drop_pending_updates=true"
```

Telegram will only deliver updates whose secret header matches; the pointer
precedence stays D1 → `MASTER_RESUME_SHA`. `deleteWebhook` is the rollback:
restoring the previous D1 pointer (or deleting the row) brings back the old
master, and no R2 object is ever deleted.

## Boundaries (non-negotiable)

- LinkedIn: discovery disabled (`LINKEDIN_ENABLED=false`); submits never automated (auth + checkpoints).
- Ashby live submit: dry-run until an org endpoint is pinned by a supervised run.
- CAPTCHA/Turnstile jobs: BLOCKED queue + alert, never bypassed.
- Rezi mirroring + heavy PDF layouts stay in the Python pipeline / Actions.
- Worker resume policy (decided 2026-10-08): master-only by design. Rezi's API
  exposes no download/export endpoint, so per-job variants cannot reach R2;
  they live on the Python/browser side only. Do not re-propose the sync
  unless Rezi ships an export tool.
