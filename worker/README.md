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
| Worker-runnable | **298** | greenhouse 106, ashby 47, lever 45, rss 17, smartrecruiters 15, linkedin (disabled), indeed, ashby-index, freehire, bdjobs, teamtailor 2, recruitee 3, personio 1, bamboohr, workable |
| Python-only | **84** | Bayt (TLS impersonation), Naukri (RSA handshake), HTML scraping (`web`) |
| Total in `sources.yaml` | **382** | every source accounted for, none silently dropped |

### Measured yield (live probe, 2026-10-02)

Configured is not the same as working. Probing all 213 ATS boards:

| Kind | Yielding | Note |
|---|---|---|
| greenhouse | 48 / 106 | GitHub, DoorDash and others migrated off Greenhouse (`figma` 200, `github` 404 — slug derivation is correct, the boards are gone) |
| ashby | 30 / 47 | incl. `ashby-index` fan-out for aggregators like jobs.solana.com |
| lever | 2 / 45 | Lever is effectively abandoned by these orgs |
| smartrecruiters | 1 / 15 | effectively dead |

Under the 12h + remote filters, **17 sources currently yield**. `GET /coverage`
reports configured vs attempted vs yielding from D1 — coverage claims are
measured, never asserted. Add a board only after `POST /probe` shows yield.

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
| `GET /coverage` | Measured coverage from the `source_health` table, by kind. |
| `GET /sources` | Configured shard: total, python-only, hourly subset, by kind. |
| `POST /run?deep=1` | Fan out the whole shard, one source per message, ≤50 per `sendBatch`. Omit `deep` for the hourly subset. |
| `POST /probe` | Read-only single-source yield check: `{"name":"stripe"}` → `raw` / `fresh_12h` / `remote_fresh`. Writes nothing. |
| `POST /queue` | Fetch jobs by state (`{"state":"ELIGIBLE","count":10}`). |
| `POST /telegram/webhook` | Owner-only Telegram inbound; `X-Telegram-Bot-Api-Secret-Token` must match `TELEGRAM_WEBHOOK_SECRET` (else 401). A PDF upload updates `settings.master_resume_sha` (precedence: D1 → `MASTER_RESUME_SHA` secret); `/master` reports the current sha + source; everything else is ignored. |

## Pipeline gotchas worth not rediscovering

- **Descriptions must be enriched.** ATS board *list* endpoints omit
  descriptions; scoring a title-only string rejects 100% of jobs for lack of
  evidence. `enrichDescriptions` fetches per-job content for the few jobs that
   survive the 12h + remote filters (bounded to 12 to stay under the
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
  and alerts `FILTERED` instead of `SEEKING`. The gate reason is recorded in the
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
  every all-remote spelling collapsed to one token (`lib/hash.ts`) — and the
  discover stage resolves the whole source's fingerprints in **one** batched
  `findKnownJobs` read (two `IN` lists, chunked at 50 bindings) before storing
  anything. A hit owned by a *different* canonical hash is stored as `DUPLICATE`
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
    impossible.
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
  credentials unset nothing is claimed. The table is created at runtime
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
notifications (`SEEKING` / `FILTERED` / `ELIGIBLE` / `TAILORED` /
`APPLICATION_ATTEMPT` / `SUBMITTED` / `DRY_RUN` / `FAILED` / `STAGE_FAILED`),
at most once per `(runtime, job_hash, event)` via the `notification_events`
ledger. Unset credentials = silent no-op, nothing claimed.

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
