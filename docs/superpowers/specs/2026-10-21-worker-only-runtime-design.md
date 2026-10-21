# Worker-only runtime (2026-10-21)

## Goal

Run the whole product on Cloudflare, 24/7, with no local machine and no second
runtime. The owner does not run the Python pipeline; keeping it meant two
divergent implementations and a nightly GitHub Actions sweep that was the only
runner for 50 sources.

## Decisions

1. **Delete the Python runtime** (`src/`, `tests/`, `scripts/`, `pyproject`,
   `uv.lock`, `.github/workflows`, local DB and reference repos). The Worker is
   the only runtime.
2. **Every non-dead configured source runs on the Worker.** The shard grows
   from 150 to **200** (375 configured, 175 dead excluded) by porting the three
   Python-only adapters:
   - `web` (48 sources): JSON-LD `JobPosting`, server-rendered job links, and
     hydrated state (`__NEXT_DATA__`, `var Stash =`). No browser.
   - `bayt` (1): search HTML cards; plain fetch (the old adapter used TLS
     impersonation because Bayt's edge blocks plain clients — a block reads
     flaky, never dead).
   - `naukri` (1): public jobapi v3 JSON + RSA PKCS#1 v1.5 request token,
     implemented with BigInt (no WebCrypto padding-mode dependency).
3. **The generator moves to Node** (`worker/scripts/gen_sources.mjs`, `yaml`
   dev dependency). It throws when an adapter has no Worker implementation, so
   a source can never silently disappear. `--check` runs in `npm test`.
4. **Operational tools move to Node**:
   - `worker/scripts/probe_sources.mjs` calls the deployed `/probe` endpoint
     for each source, writes `data/source_probe.json`, and with `--apply`
     rewrites `status:` in `config/sources.yaml` then regenerates the shard.
     LinkedIn is never auto-applied (config keeps `unknown`).
   - `worker/scripts/verify_deploy.mjs` checks `/health`, `/sources`,
     `/coverage` and optional probes; `--expect-live` asserts autonomous mode.
5. **Liveness is measured at any age** (`usable_any_age` added to `/probe`):
   the 24h window gates the feed, not the board. A failed fetch is flaky; an
   answered clean zero (or 404/410) is dead.

## Submission reality (verified, not assumed)

The investigation before enabling live submission:

| ATS | Result | Consequence |
|---|---|---|
| Greenhouse | `POST boards-api.greenhouse.io/v1/boards/{org}/jobs/{id}` with empty fields → **401 HTTP Basic: Access denied**; public form loads reCAPTCHA Enterprise | no unauthenticated submit; queued for human unless a company API key exists |
| Lever | public `/apply` accepts multipart (empty fields → 400 validation) but the page loads hCaptcha | captcha-protected boards refuse; queued |
| Ashby | `applicationForm.submit` and `applicationForm.info` → **401 Unauthorized**; job page loads reCAPTCHA | not pinned; queued |

Therefore:

- `LIVE_APPLY` stays **false** by default; `APPROVE_GREENHOUSE`/`APPROVE_LEVER`
  are off.
- `applyDisposition` classifies a live attempt: success → `SUBMITTED`;
  captcha/401/403/“access denied” → `APPLICATION_STARTED` (“apply manually”)
  with `APPLICATION_ATTEMPT`; transport/validation → `FAILED`. A refusal that
  never reached the employer is never reported as a failed application.
- Fully unattended submission to captcha-protected ATSs is **not possible on
  the Workers free tier**. It needs a paid browser/captcha service or employer
  API keys — an owner decision, not an implementation detail.

## What still works autonomously 24/7

- 200-source discovery, hourly scout + 6-hourly deep sweep.
- Deterministic evaluation, disability/eligibility gating, dedupe, state.
- Application package preparation (master PDF from R2), Telegram outcomes.
- Source health, coverage counters, probe/ledger tooling.

## Removed

Python CLI/UI, Playwright browser apply, Rezi mirroring, GitHub Actions sweep,
local SQLite state, reference repos, stale worktrees, `.venv`, screenshots.

## Risks / open items

- `web` coverage depends on server-rendered HTML; SPA-only boards read empty.
- Bayt may read flaky from Cloudflare egress (TLS fingerprint); it is measured,
  not assumed.
- Naukri's RSA token can go stale if the public key rotates (406); recorded as
  a source failure.
- Autonomous *submission* remains the open product gap described above.
