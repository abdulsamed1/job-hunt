# jobhunt Worker

The whole runtime: discovery, evaluation, application preparation and
submission attempts on Cloudflare Workers (free tier). TypeScript, D1, R2,
Queues, Cron. No browser, no Python, no server.

Live: `https://jobhunt.habdulsamed777.workers.dev`

## Layout

```
src/index.ts            fetch handler + cron + queue consumer (all stages)
src/sources.ts          SourceDef / SourceKind types
src/sources.generated.ts  GENERATED from config/sources.yaml — never hand-edit
src/stages/discover.ts  per-source fetch, filters, dedupe, store
src/stages/pipeline.ts  evaluate, PDF/text tailoring, applyToAts, applyDisposition
src/adapters/           ats.ts, boards.ts, rss.ts, tenants.ts, web.ts, bayt.ts, naukri.ts, http.ts
src/lib/freshness.ts    recency, remote detection, Egypt/geo gate
src/lib/roles.ts        software-role gate
src/lib/evaluate.ts     deterministic scorer (skills, title, location, salary)
src/lib/match.ts        answer/checkbox option matching (tested parity helper)
src/lib/hash.ts         canonical URL hash, role fingerprint
src/lib/hosts.ts        ATS host allowlist + board URL builders
src/lib/notify.ts       Telegram + at-most-once event ledger
src/lib/webhook.ts      inbound Telegram update parsing
src/lib/health.ts       degraded-source signal
src/state.ts            D1 helpers + runtime migrations
schema.sql              D1 schema (apply with --remote)
```

## Pipeline

| Stage | Queue | What happens |
|---|---|---|
| discover | `jobhunt-discover` (1 msg/source) | fetch, enrich descriptions, 24h filter, role gate, geo gate, dedupe, store, enqueue evaluations |
| evaluate | `jobhunt-evaluate` (batch 20) | deterministic score, disability/eligibility gate, save, enqueue eligible |
| tailor-apply | `jobhunt-apply` (batch 5) | re-check window/role/disability, mark `TAILORED`, attach master PDF, submit or queue |

Cron (`wrangler.toml`):

- `15 * * * *` — hourly scout: 26 curated sources (`cadence: "hourly"`).
- `0 */6 * * *` — deep sweep: all 206 Worker-runnable sources.

There is no per-stage retry loop in the Worker; queue retries are
`max_retries` on each consumer. A thrown stage error is recorded as
`STAGE_FAILED` and the message is acknowledged (no retry storm).

## Source shard

`config/sources.yaml` is the source of truth. 375 entries → 206 in the
shard → 147 yielding usable postings at the last probe (2026-10-21); 169 dead
boards are excluded from polling but remain probe-able.

```bash
node scripts/gen_sources.mjs          # write src/sources.generated.ts
node scripts/gen_sources.mjs --check  # exit 1 on drift
```

The generator emits every supported source with its status (dead included).
The pipeline runs only non-dead entries; `/probe` can still reach a dead row
(`ALL_SOURCES`), so a recovered board is promoted without a code change. It
**throws** when an active source's kind has no Worker implementation, so a
source can never be silently dropped. Coverage accounting in `/sources` and
`/coverage` is measured from D1, not asserted.

### Filters (in order)

1. **Recency** — 24h (`HOURS_OLD`); dateless postings are stored but never
   scored/applied. JSON/rss feeds (RemoteOK, Remotive, Jobicy, Arbeitnow) are
   parsed as JSON first, then RSS/Atom. Greenhouse `updated_at` is used as a proxy when needed.
2. **Role** — software only; leadership/BD/CRM/non-engineering rejected. A
   domain word (finance, legal, medical, accounting) rejects only when the
   title names no engineering build role.
3. **Geography** — keep remote anywhere, keep Egypt (on-site/hybrid/remote),
   keep unknown; drop only proven on-site outside Egypt.
4. **Dedupe** — canonical URL hash + role fingerprint (company/title/place).
   Ownership is narrow: only actionable rows block a twin.
5. **Intake cap** — 150 postings per source per run (`INTAKE_CAP`), ordered by
   recency. It is a flood guard, not a throttle.

## Data model (D1)

| Table | Purpose |
|---|---|
| `jobs` | one row per canonical posting; state machine (`DISCOVERED` → … → `SUBMITTED`/`FAILED`) |
| `evaluations` | score, eligible, reasoning per job |
| `applications` | state, detail, resume pointer per job (unique) |
| `source_health` | attempts, raw/kept counts, last error, last seen |
| `counters` | `role_rejected`, `duplicate_suppressed`, `dateless_dropped`, `intake_capped` |
| `settings` | master resume pointer (`master_resume_sha`) |
| `notification_events` | at-most-once Telegram ledger |
| `audit_log` | state transitions |

`schema.sql` is idempotent; `state.ts` also runs lightweight migrations at
runtime (`ensureRoleFingerprint`, `ensureNotificationEvents`, …).

## Endpoints

| Path | Method | Notes |
|---|---|---|
| `/health` | GET | `ok`, job count, profile skills, `live_apply`, `notification_events`, `master_resume_sha` |
| `/recent` | GET | recent jobs; includes `actionable` |
| `/coverage` | GET | attempted/yielding sources, dated/proxy fresh, filter counters, live software companies |
| `/sources` | GET | shard size, hourly count, per-kind counts |
| `/probe` | POST `{name}` | read-only fetch+filter; `usable_any_age`, `usable_fresh`, `remote_fresh`, sample, or `inconclusive` |
| `/queue` | POST | enqueue `{stage, ...}` |
| `/run` | POST | fan out the whole shard |
| `/telegram/webhook` | POST | owner-only inbound PDF → master resume; `/master` command reports the pointer |

## Apply contract

`applyToAts` (in `stages/pipeline.ts`) supports:

- **Greenhouse** — `POST https://boards-api.greenhouse.io/v1/boards/{org}/jobs/{id}`
  multipart. **Requires per-company Basic auth**; unauthenticated is 401.
- **Lever** — `POST https://api.lever.co/v0/postings/{org}/{id}`? No: the
  public apply endpoint is `https://jobs.lever.co/{org}/{postingId}/apply`
  (multipart). Forms load hCaptcha; a captcha-protected board refuses.
- **Ashby** — not pinned; always queued (`dry-run only`).

`LIVE_APPLY=false` by default. Live requires `APPROVE_GREENHOUSE` or
`APPROVE_LEVER` in addition. `applyDisposition` maps a refusal:

- captcha / 401 / 403 / “access denied” → `APPLICATION_STARTED` (“apply
  manually”) + `APPLICATION_ATTEMPT` — nothing was submitted.
- transport/validation failure → `FAILED`.
- success → `SUBMITTED`.

Never report `SUBMITTED` unless the ATS answered success. Never bypass a
captcha.

**LinkedIn** is discovery-only: the guest search endpoint is used, no account
or cookies; the tailor-apply stage queues LinkedIn rows for the human and never
calls `applyToAts`.

## Telegram

Outcome events only by default:
`APPLICATION_ATTEMPT`, `SUBMITTED`, `DRY_RUN`, `FAILED`, `STAGE_FAILED`.
`TELEGRAM_EVENTS` overrides (comma-separated). The ledger claims the key before
sending (at-most-once); a muted event never claims, so it stays silent if later
enabled.

Inbound updates are accepted only from the owner's chat and only a PDF
document (or `/master`). The PDF is written to
`cvs/master/<sha256>.pdf` and `settings.master_resume_sha` is updated.

## Local commands

```bash
npm ci
npm test                 # 304 tests
npx tsc --noEmit
node scripts/gen_sources.mjs
node scripts/probe_sources.mjs --apply
node scripts/verify_deploy.mjs --new-only
npx wrangler deploy
```

`verify_deploy.mjs` checks `/health`, `/sources`, `/coverage` and optional
`/probe` results. It fails on unreachable endpoints, wrong safety flags, a
missing/invalid master resume pointer, or a transport error; quiet boards warn.

## Gotchas

- ATS list endpoints omit descriptions (greenhouse, smartrecruiters) — enrich
  before scoring or everything rejects for lack of evidence.
- `upsertJob` must backfill richer text on a repeated URL.
- Discover consumer `max_batch_size = 1`: one source per invocation so
  `INTAKE_CAP` postings fit the subrequest budget.
- `wrangler d1 execute` needs `--remote`; retry transient `fetch failed`.
- `wrangler deploy` fails with "The request to Cloudflare's API timed out" when
  the local route to some `api.cloudflare.com` anycast IP is broken (TCP connect
  hangs past undici's 10s connect timeout). Check with
  `node -e "fetch('https://api.cloudflare.com/client/v4/')"`; retry, or pin the
  host to a healthy edge IP. The Worker's own endpoints are unaffected.
- `web` boards whose postings only exist after a client-side API call return
  an empty list (no browser on the Worker). JSON-LD, server-rendered links and
  `__NEXT_DATA__`/`var Stash` hydrated state are supported.
- Bayt's edge blocks plain clients (the old Python adapter used TLS
  impersonation); a Worker fetch may read flaky. Naukri uses the public JSON
  API with an RSA request token (BigInt, no WebCrypto padding dependency).
