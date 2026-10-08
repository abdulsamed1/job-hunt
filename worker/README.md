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
| Worker-runnable | **241** | greenhouse 106, ashby 47, lever 45, rss 17, smartrecruiters 15, linkedin (disabled), indeed, ashby-index, freehire, bdjobs, teamtailor 2, recruitee 3, personio 1 |
| Python-only | **84** | Bayt (TLS impersonation), Naukri (RSA handshake), HTML scraping (`web`) |
| Total in `sources.yaml` | **325** | every source accounted for, none silently dropped |

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
- `src/state.ts` — D1 state machine (mirrors the SQLite lifecycle)
- `src/sources.ts` — `SourceDef` type; `src/sources.generated.ts` — GENERATED, do not hand-edit
- `src/index.ts` — cron dispatcher (hourly shard + daily deep), queue
  consumers, HTTP API (see below)
- `scripts/gen_sources.py` — `sources.yaml` → `sources.generated.ts` generator
- `test/` — vitest suites (`npm test`), `tsconfig.json` — `npm run typecheck`

## HTTP API

| Endpoint | Purpose |
|---|---|
| `GET /health` | Job count **+ profile sanity**: `profile_skills`, `profile_languages`, `threshold`, `live_apply`. An empty `verified_skills` silently rejects every job — check this first. |
| `GET /recent` | `actionable` (tailored / submitted / human queue), `eligible`, `latest`. **Read `actionable`** — qualifying jobs leave the `ELIGIBLE` state once tailored, so `eligible` alone always looked empty. |
| `GET /coverage` | Measured coverage from the `source_health` table, by kind. |
| `GET /sources` | Configured shard: total, python-only, hourly subset, by kind. |
| `POST /run?deep=1` | Fan out the whole shard, one source per message, ≤50 per `sendBatch`. Omit `deep` for the hourly subset. |
| `POST /probe` | Read-only single-source yield check: `{"name":"stripe"}` → `raw` / `fresh_12h` / `remote_fresh`. Writes nothing. |
| `POST /queue` | Fetch jobs by state (`{"state":"ELIGIBLE","count":10}`). |

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
```

Live applies stay OFF until a human flips per-board flags:

```bash
npx wrangler secret put LIVE_APPLY        # "true" only when supervised
npx wrangler secret put APPROVE_GREENHOUSE # per-board approval
npx wrangler secret put APPROVE_LEVER
```

Optional: `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` for match alerts.

## Boundaries (non-negotiable)

- LinkedIn: discovery disabled (`LINKEDIN_ENABLED=false`); submits never automated (auth + checkpoints).
- Ashby live submit: dry-run until an org endpoint is pinned by a supervised run.
- CAPTCHA/Turnstile jobs: BLOCKED queue + alert, never bypassed.
- Rezi mirroring + heavy PDF layouts stay in the Python pipeline / Actions.
