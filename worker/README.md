# jobhunt-scout — Cloudflare Worker companion (free tier)

Hourly 24/7 discovery shard: cheap stateless sources (RSS/JSON feeds + one
LinkedIn guest page per query) → keyword score → D1 upsert → optional Telegram
alerts. No browser, no PDF, no LLM — heavy stages stay in GitHub Actions.

## Why this split (free-tier math)

| Piece | Where | Why it fits |
|---|---|---|
| Hourly scout (~30 req/h ≈ 720/day) | Worker + cron | 100k req/day free; D1 writes only for new jobs |
| Full 317-source sweep + browser | GitHub Actions schedule | 2000 min/mo free; Playwright impossible on Workers |
| Secrets | `wrangler secret` / GitHub Secrets | never in git |

## Deploy (3 commands after `wrangler login`)

```bash
cd worker
npx wrangler d1 create jobhunt-scout
# paste the returned database_id into wrangler.toml
npx wrangler d1 execute jobhunt-scout --file schema.sql
npx wrangler deploy
```

Optional alerts (Telegram):

```bash
npx wrangler secret put TELEGRAM_BOT_TOKEN
npx wrangler secret put TELEGRAM_CHAT_ID
```

Rezi token is NOT needed here (mirroring stays in the pipeline).

## Endpoints

- `GET /health` — `{ ok, jobs }` row count
- `GET /recent` — last 50 discoveries as JSON
- `POST /run` — trigger a shard manually (then check `/recent`)

## Notes

- LinkedIn guest fetch is 1 page/query/hour on purpose: CF IPs are
  rate-limit sensitive; the Python pipeline remains the deep source.
- Cron runs at minute 15 hourly (`15 * * * *`); adjust in `wrangler.toml`.
- D1 free quotas (5M reads, 100k writes/day) exceed this workload ~100x.
