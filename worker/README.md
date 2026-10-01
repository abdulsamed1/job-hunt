# jobhunt — full autonomous pipeline on Cloudflare Workers (free tier)

24/7 job capture + application across all sources with **zero browser**:
discovery → evaluate → tailor (text CV + PDF) → direct ATS HTTP applies,
with LinkedIn Easy Apply and CAPTCHA jobs held for humans.

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
  Greenhouse/Lever/Ashby/SmartRecruiters APIs, LinkedIn guest shard
- `src/lib/` — hash, freshness, gates, deterministic scoring, honest Q&A mapping
- `src/stages/pipeline.ts` — evaluate, tailor (pdf-lib PDF → R2), ATS apply
- `src/state.ts` — D1 state machine (mirrors the SQLite lifecycle)
- `src/index.ts` — cron dispatcher (hourly shard + daily deep), queue
  consumers, HTTP API (`/health` `/recent` `/queue` `/run`)
- `test/` — vitest suites (`npm test`), `tsconfig.json` — `npm run typecheck`

## Deploy

```bash
cd worker
npm install
npm test && npm run typecheck
npx wrangler d1 create jobhunt
# paste database_id into wrangler.toml, then:
npx wrangler d1 execute jobhunt --file schema.sql
npx wrangler kv:namespace create CV_BUCKET  # not needed: R2 below
npx wrangler r2 bucket create jobhunt-cvs
npx wrangler queues create jobhunt-discover
npx wrangler queues create jobhunt-evaluate
npx wrangler queues create jobhunt-apply
npx wrangler secret put PROFILE_JSON   # paste candidate_profile.json content
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

- LinkedIn submits: never automated (auth + checkpoints). Guest discovery only.
- Ashby live submit: dry-run until an org endpoint is pinned by a supervised run.
- CAPTCHA/Turnstile jobs: BLOCKED queue + alert, never bypassed.
- Rezi mirroring + heavy PDF layouts stay in the Python pipeline / Actions.
