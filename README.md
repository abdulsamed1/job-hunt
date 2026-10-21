# jobhunt — 24/7 autonomous job search on Cloudflare Workers

Discovery, deterministic evaluation and application preparation for software
roles — remote anywhere, or on-site/hybrid/remote in Egypt — running entirely
on the Cloudflare free tier. No server, no browser, no paid APIs, nothing to
keep running on a laptop.

Live deployment: `https://jobhunt.habdulsamed777.workers.dev`

## What runs where

| Piece | Cloudflare service | Purpose |
|---|---|---|
| Pipeline | Worker (`worker/src`) | fetch sources, filter, score, build application packages |
| State | D1 (`jobhunt`) | jobs, evaluations, applications, source health, counters |
| Resumes | R2 (`jobhunt-cvs`) | master PDF (`cvs/master/<sha>.pdf`) |
| Fan-out | Queues | discover → evaluate → tailor/apply, one source per message |
| Schedule | Cron triggers | hourly scout (25 sources) + 6-hourly deep sweep (200 sources) |
| Alerts | Telegram | outcome events only; inbound PDF updates the master resume |
| Ledger | `data/source_probe.json` + `config/sources.yaml` | measured source status |

## Pipeline

```
cron 15 * * * *   → 25-source hourly scout   ┐
cron 0 */6 * * *  → 200-source deep sweep   ┘
        │
        ▼
DISCOVER_Q (1 source per message)
  fetch → 24h freshness → role gate → geography gate
        → dedupe (URL hash + role fingerprint) → D1
        │  one message per new posting
        ▼
EVALUATE_Q
  deterministic score vs PROFILE_JSON → threshold (70)
  disability / eligibility / language gate → PRE_FILTERED_OUT or eligible
        │
        ▼
APPLY_Q
  re-check freshness + role + disability → TAILORED
  attach master resume from R2 → direct ATS attempt or human queue
```

Everything is deterministic and auditable: no LLM is required (an optional
`LLM_RATIONALE` path exists but is off), and the full state machine lives in D1.

## Sources

`config/sources.yaml` is the single source of truth. The Worker shard is
generated, never hand-edited:

```bash
cd worker
node scripts/gen_sources.mjs          # regenerate src/sources.generated.ts
node scripts/gen_sources.mjs --check  # fail on drift (also runs in npm test)
```

Measured on 2026-10-21 (Worker probe, all 375 boards):

| Split | Count |
|---|---|
| Configured in YAML | **375** |
| Worker-runnable (non-dead, in the shard) | **206** |
| Yielding ≥1 usable posting at any age | **146** |
| Quiet / flaky (still in the shard, re-probed) | **60** |
| Dead (404/410 or excluded, not polled) | **169** |

Kinds on the Worker: `greenhouse`, `ashby`, `lever`, `rss`/feed,
`smartrecruiters`, `teamtailor`, `recruitee`, `personio`, `bamboohr`,
`workable`, `freehire`, `bdjobs`, `indeed`, `ashby-index`, `linkedin` (guest,
discovery only), `web` (generic HTML/JSON-LD), `bayt`, `naukri`.

Liveness rule: a source is live when it yields at least one **usable** posting
**at any age** — remote anywhere, placed in Egypt (on-site, hybrid or remote),
or unknown location. The 24h window gates the *feed*, not the board. A failed
fetch is flaky (`unknown`), never a stale live. A quiet board stays in the
shard and is re-probed; only an answered 404/410 demotes it to `dead`. Dead
boards remain probe-able and are promoted back to `live` when they answer with
usable postings again.

Probe and verify against the live Worker:

```bash
cd worker
npm run probe -- --apply          # measure all 375, promote live / demote hard-dead, regenerate
npm run probe -- --only stripe,figma
npm run verify -- --new-only      # post-deploy check (add --expect-live for live deploys)
```

## Deploy

The runtime needs no local machine. Two equivalent paths:

**Cloudflare Workers Builds (recommended).** Connect the GitHub repo in the
Cloudflare dashboard, set the root directory to `worker`, the build command to
`npm ci && npm test`, and the deploy command to `npx wrangler deploy`. Every
push to `main` ships after tests pass.

**CLI, from any machine:**

```bash
cd worker
npm ci
npm test
npx tsc --noEmit
npx wrangler deploy
node scripts/verify_deploy.mjs --new-only --expect-live
```

One-time provisioning (already done for the live deployment):

```bash
cd worker
npx wrangler d1 create jobhunt
npx wrangler d1 execute jobhunt --remote --file schema.sql   # --remote is required
npx wrangler queues create jobhunt-discover
npx wrangler queues create jobhunt-evaluate
npx wrangler queues create jobhunt-apply
npx wrangler r2 bucket create jobhunt-cvs
```

Secrets (`npx wrangler secret put …`): `PROFILE_JSON`, `MASTER_RESUME_SHA`,
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_WEBHOOK_SECRET`.

Non-secret vars live in `worker/wrangler.toml` (`THRESHOLD`, `INTAKE_CAP`,
`LIVE_APPLY`, `APPROVE_GREENHOUSE`, `APPROVE_LEVER`, `LINKEDIN_ENABLED`,
`TELEGRAM_EVENTS`, …).

## Endpoints

| Endpoint | Method | What it returns |
|---|---|---|
| `/health` | GET | job count, profile skills, live-apply flag, resume pointer, ledger count |
| `/coverage` | GET | attempted/yielding sources, fresh counts, filters, live software companies |
| `/sources` | GET | shard size, hourly count, per-kind counts |
| `/probe` | POST `{name}` | read-only yield probe for one source (`usable_any_age`, `usable_fresh`, sample) |
| `/recent` | GET | recent jobs, including `actionable` |
| `/queue` | POST | enqueue a stage message |
| `/run` | POST | fan out the shard (manual trigger) |
| `/telegram/webhook` | POST | owner-only inbound PDF → master resume pointer |

## Submitting applications — the honest part

The product prepares everything automatically, but **direct submission is
gated by what each ATS allows a server to do**:

- **Greenhouse** — the public board API (`POST /v1/boards/{org}/jobs/{id}`)
  requires HTTP Basic auth with a per-company API key; an unauthenticated POST
  returns 401. The public application form is reCAPTCHA-protected. The Worker
  therefore queues Greenhouse postings for the human unless a company API key
  is supplied.
- **Lever** — the public `/apply` endpoint accepts multipart POSTs but the
  board forms load hCaptcha; captcha-protected boards are queued for the human.
- **Ashby** — the submit contract is not pinned and the public form is
  reCAPTCHA-protected; Ashby is queued.
- **Everything else** (web, RSS, Bayt, Naukri, LinkedIn, …) has no
  server-side apply contract and is queued.

The code encodes this: `LIVE_APPLY=false` by default; live sends require the
per-board `APPROVE_*` flag; a captcha/credential refusal becomes
`APPLICATION_STARTED` (“apply manually”) with an `APPLICATION_ATTEMPT` alert,
not a fake `FAILED`; only a real transport/validation failure is `FAILED`.
Fully unattended submission to captcha-protected ATSs requires a paid browser
or captcha-solving service — it is not possible on the Workers free tier.

**LinkedIn is discovery-only.** Search uses the public guest endpoint (no
cookies, no account). No automated path applies through the owner's LinkedIn
account — that is a permanent safety rule after the 2026-10-02 ban.

## Notifications

Telegram receives outcome events only by default:
`APPLICATION_ATTEMPT`, `SUBMITTED`, `DRY_RUN`, `FAILED`, `STAGE_FAILED`.
Stage chatter (`SEEKING`, `FILTERED`, `ELIGIBLE`, `TAILORED`) is muted.
Override with `TELEGRAM_EVENTS` (a muted event is never sent and never claims
the at-most-once ledger row).

Inbound: only the owner's chat can upload a PDF, which replaces the master
resume pointer (`settings.master_resume_sha` in D1); `/master` reports it.

## Testing

```bash
cd worker
npm test            # 296 tests, includes source-shard drift + parser fixtures
npx tsc --noEmit
```

## Limits

- No browser: postings that require JavaScript-only apply flows are queued.
- No per-job Rezi variants: every application uses the master PDF in R2.
- No LinkedIn apply.
- Free-tier budgets: cron + queues + D1/R2; the pipeline is sized for them
  (`INTAKE_CAP=150`, one source per discover message).

## Repository map

```
config/sources.yaml          source of truth (375 entries)
data/source_probe.json       measured liveness ledger
worker/src/                  Worker runtime
worker/src/adapters/         per-kind fetchers (ATS, RSS, web, Bayt, Naukri, LinkedIn guest)
worker/src/lib/              freshness, roles, scoring, hashing, hosts, notify
worker/src/stages/           discover, evaluate, tailor/apply
worker/scripts/              source generator, probe, deploy verifier
worker/schema.sql            D1 schema
worker/test/                 vitest suites
docs/superpowers/specs/      design records
```
