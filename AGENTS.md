# AGENTS.md

Autonomous job application engine. **Cloudflare Worker only** — TypeScript,
D1, R2, Queues, Cron. There is no Python runtime, no local pipeline and no
browser. Do not reintroduce one without an explicit owner decision.

## Commands

```bash
cd worker
npm ci
npm test                 # vitest, includes source-shard drift + parser fixtures
npx tsc --noEmit         # typecheck
npm run sources          # regenerate src/sources.generated.ts
npm run sources:check    # drift check (also runs in npm test)
npm run probe -- --apply # measure all 375 boards, promote live / demote hard-dead
npm run probe -- --only <name>        # measure one board
npm run verify -- --sources a,b       # post-deploy check (--expect-live for live)
npx wrangler deploy
```

`probe` and `verify` talk to the deployed Worker over an explicit IPv4 socket
(`node:https`, `family: 4`) and retry a stalled connect once — a transport
blip is not a verdict and must never be recorded as one.

## Runtime

```
cron hourly (26) + 6-hourly (206)
  → DISCOVER_Q (1 source/message) → D1 jobs
  → EVALUATE_Q (deterministic score + gates) → eligible
  → APPLY_Q (re-gates, attaches master PDF from R2) → submit or human queue
```

- D1 `jobhunt`: `jobs`, `evaluations`, `applications`, `source_health`,
  `counters`, `settings`, `notification_events`, `audit_log`.
- R2 `jobhunt-cvs`: `cvs/master/<sha>.pdf` — the only resume bytes the Worker
  attaches. No per-job Rezi variants.
- Telegram: outcome events only by default (`APPLICATION_ATTEMPT`, `SUBMITTED`,
  `DRY_RUN`, `FAILED`, `STAGE_FAILED`); `TELEGRAM_EVENTS` overrides. Muted
  events never claim the at-most-once ledger row.
- Live: `https://jobhunt.habdulsamed777.workers.dev`.

## Source coverage is generated (never hand-kept)

- `config/sources.yaml` is the single source of truth. **375** entries:
  **206 Worker-runnable** (in the shard) + **169 dead** (not polled).
  Measured live on 2026-10-21: **147** (≥1 usable posting at any age); 59
  quiet/flaky boards stay in the shard and are re-probed.
- `worker/scripts/gen_sources.mjs` emits `worker/src/sources.generated.ts` and
  fails when an adapter has no Worker implementation. Never hand-edit the
  generated file.
- The generated file ships **every supported source with its status**, dead
  ones included. The pipeline runs only non-dead entries (`status !== "dead"`);
  `/probe` searches all of them, so a board that comes back to life is
  promoted by a status change, not by a hand-edited special case.
- `worker/src/sources.generated.ts` is checked in; `npm test` runs `--check`.
- Kinds: greenhouse, ashby, lever, rss/feed, smartrecruiters, teamtailor,
  recruitee, personio, bamboohr, workable, freehire, bdjobs, indeed,
  ashby-index, linkedin (guest), web, bayt, naukri.
- Hourly shard = 26 curated sources. Everything else runs on the 6-hourly
  deep sweep. The deep sweep is 4×/day so a 24h window never misses a posting.
- Adding a source: add to `config/sources.yaml` → implement the adapter if the
  kind is new → `npm run sources` → `npm test` → deploy →
  `npm run probe -- --only <name>` → keep it only if `usable_any_age > 0`
  (or leave `status: unknown` while it is being evaluated). A board that
  answers but posts nothing stays configured and is re-probed; only a hard
  404/410 is written back as `status: dead`.
- Probing is triage, not the product. `data/source_probe.json` is the ledger.
  Liveness = at least one usable posting at **any age** (remote anywhere,
  placed in Egypt, or unknown location). A failed fetch is flaky (`unknown`),
  never dead. A quiet board is kept and re-probed; only a 404/410 demotes it
  to `dead`. Dead boards stay probe-able (`ALL_SOURCES`) and revive on a
  yielding probe.
- **"Live" is not "productive".** A board that posts without a date (most
  `web` boards) counts as live but its rows are dateless, so they are stored
  for the record and never scored. The live count therefore overstates the
  24h feed; the productive set is the boards that also yield *dated* rows.

## Geography and recency

- 24h recency everywhere (`HOURS_OLD = 24` on the Worker). Dateless postings
  are stored for the record, never scored or applied.
- Geography: keep remote anywhere, keep anything placed in Egypt (on-site,
  hybrid or remote), keep unknown locations; drop only a posting proven
  on-site outside Egypt. Helpers: `worker/src/lib/freshness.ts`
  (`filterTargetGeoList`, `isEgyptLocation`, `isRemoteish`).
- Role gate: software only; rejects leadership, BD/CRM and non-engineering
  titles. A domain word rejects only when the title names no engineering build
  role (“Full Stack Engineer - Finance” passes, “Senior Accountant” does not).
- Dedupe by canonical URL hash + role fingerprint; a filtered row owns no
  fingerprint (so a re-post is not lost).

## Applying — hard rules

1. **LinkedIn is discovery-only, forever.** Search uses the public guest
   endpoint (no cookies, no login, no account). No automated path may apply
   through the owner's LinkedIn account; the tailor-apply stage queues a
   LinkedIn row as `APPLICATION_STARTED` and never calls an ATS client.
2. **Never fake a submission.** `LIVE_APPLY=false` is the default. A live send
   requires the board to be approved (`APPROVE_GREENHOUSE`/`APPROVE_LEVER`)
   *and* a known-good endpoint. A captcha/credential refusal is
   `APPLICATION_STARTED` (“apply manually”) with `APPLICATION_ATTEMPT`, not
   `SUBMITTED`; only a real transport/validation failure is `FAILED`
   (`applyDisposition` in `worker/src/stages/pipeline.ts`).
3. **Known ATS limits** — do not “fix” these by pretending:
   - Greenhouse board API POST requires per-company Basic auth; unauthenticated
     is 401. Public forms are reCAPTCHA-protected.
   - Lever public `/apply` accepts multipart but forms load hCaptcha.
   - Ashby submit contract is unpinned; public forms are reCAPTCHA-protected.
   - Fully unattended captcha-protected submission is not possible on the free
     tier. It needs a paid browser/captcha service or employer API keys.
4. **No captcha bypass.** Do not add audio-solving, token farms, or spoofing.
5. **No hidden text, no keyword stuffing.** ATS keyword lines (when used) must
   be visible and contain only verified profile skills.
6. **Secrets never in git.** `PROFILE_JSON`, `MASTER_RESUME_SHA`,
   `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_WEBHOOK_SECRET` live in
   `wrangler secret`; never print them.
7. **Rezi writes are create-only and off-Worker.** The Worker does not call
   Rezi; it attaches the master PDF.
8. **Every code change needs tests.** Red → green; `npm test` and
   `npx tsc --noEmit` must be clean before deploy.
9. **Owner decision (2026-10-21): one-click manual.** `LIVE_APPLY` stays
   `false` and every eligible posting is queued for the human. The queued
   alert *is* the product: it must carry the posting URL and say what to do
   (`manualApplyDetail` in `worker/src/stages/pipeline.ts` →
   `apply manually: tap the link, attach your master CV (master CV <sha8>)`).
   A paid captcha service and employer API keys were the rejected
   alternatives; reviving either needs an explicit owner decision, and no
   captcha bypass may be added in the meantime (rule 4).

## Worker gotchas that cost real debugging time

- ATS list endpoints omit descriptions (greenhouse/smartrecruiters) → must
  enrich before scoring, otherwise every job rejects for lack of evidence.
- ATS board caps are **300**, not 40. Remote roles sit deep in big boards
  (Cloudflare's only remote role is #284, Datadog's #53, Brex's #143). A
  lower cap silently hides them and the board reads as dead.
- The `rss` kind is a **generic feed**: JSON is parsed first (RemoteOK,
  Remotive, Jobicy, Arbeitnow), then RSS/Atom. An XML-only parser returns 0
  for a JSON feed and the board reads as quiet, not broken.
- Feed bodies can outgrow the 1.5MB page cap (Arbeitnow serves 2.3MB), so
  feeds use a 3MB cap. Too large must read as flaky, never as dead.
- `web` boards: search and category links (`/jobs/q-…`, `/job-search/…`) must
  never be stored as postings — see `NOT_A_JOB_PATH` in
  `worker/src/adapters/web.ts`. They are dateless, so they never score, but
  they do inflate the jobs table.
- A posting is an ATS board only when its `source` column is
  `greenhouse:org` / `lever:org` / `ashby:org`. Everything else (web, RSS,
  Bayt, Naukri, LinkedIn) is queued for the human by design.
- `upsertJob` must backfill richer text when the same URL arrives again.
- `/run` must fan out the whole shard, not just the hourly slice.
- Empty skill evidence must never become eligible.
- Discovery queue consumer is `max_batch_size = 1` so a message can store up to
  `INTAKE_CAP` postings without exhausting the subrequest budget.
- `d1 execute` needs `--remote`; retry transient `fetch failed` on writes.
- Node's global `fetch` can hang on a broken IPv6 route (ETIMEDOUT, ~10s).
  The probe and verify scripts use `node:https` with `family: 4` instead; do
  not reintroduce global `fetch` there.
- `wrangler deploy` fails with "The request to Cloudflare's API timed out"
  when the local route to some `api.cloudflare.com` anycast IP is broken
  (TCP connect hangs past undici's 10s connect timeout). Probe it with
  `node -e "fetch('https://api.cloudflare.com/client/v4/')"`, then retry or pin
  the host to a healthy edge IP. The Worker's own endpoints are unaffected.
- Wrangler reports a pre-existing libc field drift in `package-lock.json` on
  some npm versions — do not “fix” unrelated lockfile churn in a feature PR.

## Verification

After any deploy:

```bash
cd worker
npm run verify -- --sources a,b          # check the endpoints and probe a few boards
npm run verify -- --new-only --expect-live
```

Fails on unreachable endpoints, wrong safety flags, a missing resume pointer,
or a probe transport error. Quiet/inconclusive boards warn, they do not fail.
A stalled connect is retried once before it is called a failure.

## Repository map

```
config/sources.yaml          source of truth
data/source_probe.json       measured source ledger
worker/src/adapters/         per-kind fetchers (ats, boards, rss, web, bayt, naukri)
worker/src/lib/              freshness, roles, scoring, hashing, hosts, notify
worker/src/sources.generated.ts  the shard (generated; never hand-edited)
worker/src/stages/           discover, evaluate, tailor/apply
worker/scripts/              generator, probe, deploy verifier
worker/schema.sql            D1 schema
worker/test/                 vitest suites (+ shared role/disability fixtures)
docs/superpowers/specs/      design records
```
