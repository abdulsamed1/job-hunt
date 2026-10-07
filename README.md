# Autonomous Job Application Agent for Software Engineers

An enterprise-grade, 24/7 autonomous job discovery, evaluation, CV tailoring, and browser-automated submission engine operating at **$0 marginal cost** for Senior Backend, Distributed Systems, and Infrastructure Software Engineers.

---

## High-Level Architecture

```
                [319 Configured Sources & Job Boards]
     (235 Worker-runnable + 84 Python-only; LinkedIn [disabled 2026-10-02],
      Greenhouse, Ashby,
      Lever, SmartRecruiters, RSS/JSON, Indeed, Bayt, Naukri, HTML scrape)
                                                │
                                                ▼
                                    [High-Throughput Discovery]
                                    (500+ daily quota, anti-429 rotation)
                                                │
                                                ▼
                             [Normalization & Multi-Signal Deduplication]
                       (RFC 3986 canonical URL + role fingerprint + content hash)
                                                │
                          ┌─────────────────────┴─────────────────────┐
                          ▼                                           ▼
                 [Duplicate Detected]                        [Fresh Requisition]
             (Provenance Recorded, Stop)                              │
                                                                      ▼
                                                         [Zero-Token Liveness Filter]
                                                        (Zero-token 404 / closed check)
                                                                      │
                                                                      ▼
                                                      [Deterministic Pre-Filtering]
                                                     (Positive SWE regex, negative role filter)
                                                                      │
                                                                      ▼
                                                      [Deterministic Evaluation]
                                          (skills alignment, seniority, location,
                                       sponsorship, work-auth, language, blocked lists)
                                                                      │
                          ┌───────────────────────────────────────────┴───────────────────────┐
                          ▼                                                                   ▼
                [Fit Score < 70%]                                                    [Fit Score >= 70%]
             (State: REJECTED, Stop)                                                 (State: ELIGIBLE)
                                                                                              │
                                                                                              ▼
                                                                                  [Fact-Preserving CV Stamping]
                                                                   (Master PDF copy + visible skill line)
                                                                                              │
                                                                                              ▼
                                                                                   [Playwright Stealth Engine]
                                                                               (Same-tab nav, cookie bypass, iframes)
                                                                                              │
                                                                                              ▼
                                                                                   [Multi-Step Form Filling]
                                                                                (Precision selectors, questionnaires)
                                                                                              │
                                                                                              ▼
                                                                                  [Autonomous Submission & Proof]
                                                                                (Screenshot proof + DOM confirmation)
                                                                                              │
                                                                                              ▼
                                                                             [Mission Control Web UI Dashboard]
                                                                             (http://localhost:8000 // DESIGN.md)
```

---

## Core Capabilities & Operational Moats

### 1. High-Throughput Multi-ATS Discovery (319 Configured Sources)
- **Extensive ATS Coverage**: Native adapters for **LinkedIn** (disabled 2026-10-02, account ban), **Greenhouse**, **Ashby**, **Lever**, **SmartRecruiters**, **Workday**, **Workable**, **BambooHR**, and **RSS/XML feeds**.
- **Dedicated LinkedIn Guest Scraper** (DISABLED — account ban 2026-10-02; `LINKEDIN_ENABLED=false`): Direct pagination against public guest search endpoints (`seeMoreJobPostings/search`) with automated backoff, anti-429 rotation, and zero login credentials required. Standing target when re-enabled: `backend` / `fullstack` / `software`, past-12h window (`f_TPR=r43200`), worldwide geo radius — ~60 fresh postings per run.
- **Daily Target Tracking**: Real-time velocity tracking ensuring fresh engineering requisitions are ingested and analyzed around the clock.
- **Coverage is measured, not assumed**: Probing all 213 configured ATS boards live on 2026-10-02 showed only **81 return any jobs**; 132 org slugs are dead (GitHub, DoorDash, Canva and 40+ Lever boards migrated off those ATSes — `figma` resolves while `github` 404s, so slug derivation is correct and the boards are genuinely gone). SmartRecruiters is 1/15, Lever 2/45. `GET /coverage` reports configured vs attempted vs yielding from D1 rather than trusting the config.

### 2. Multi-Signal Deduplication & Provenance
- **Canonical URL Normalization**: Strips RFC 3986 tracking parameters (`utm_*`, `gh_src`, `ref`, `source`, `fbclid`).
- **Role Identity Fingerprinting**: Normalizes corporate legal variants (`Stripe, Inc.` $\to$ `stripe`), cleans title synonyms, and hashes normalized job location.
- **Provenance Ledger**: Retains full cross-channel discovery history without re-processing duplicates.

### 3. Zero-Token Liveness & Repost Detection
- **Liveness Gate**: Performs fast HTTP HEAD/GET checks to immediately filter out 404s, expired postings, and filled jobs before spending LLM tokens.
- **Stale Job / Repost Detector**: Detects recycled requisitions across dates and avoids applying to expired reposts.

### 4. Honest ATS Tailoring (no hidden text)
- **Single Source of Truth**: The candidate master PDF is strictly preserved. Tailoring writes a *copy*; the master is never modified.
- **Visible verified-skill line only**: `append_keyword_line` appends one visible line containing **only skills already verified on the profile**. No white text, no 1pt stamps, no invisible layers, no keyword stuffing.
- **Gaps stay missing**: Skills the candidate does not have are never added to reach a match count.
- **Text-layer verification**: `verify_pdf_text_layer` confirms what a real ATS parser would extract, so claims are checked rather than assumed.
- **Why this is deliberate**: hidden text and keyword stuffing are never used — they produce rejections, misrepresent the candidate, and violate the standing rule in [`AGENTS.md`](AGENTS.md).

### 5. Robust Browser Automation Engine
- **Anti-Bot Stealth**: Injects evasions to neutralize `navigator.webdriver`, mock runtime plugins, and bypass client-side bot detection.
- **Same-Tab Navigation (`_drop_new_tabs`)**: Strips `target="_blank"` and traps `window.open` calls so navigation stays within the active Playwright session.
- **Cookie Consent Dismissal**: Automatically detects and dismisses OneTrust, Cookiebot, and GDPR overlays preventing click-interception errors.
- **IFrame Traversal**: Detects embedded application forms inside iframes (common in Greenhouse and Workable widgets) and operates directly in the child frame.
- **Multi-Step Stepper Progression**: Detects "Next", "Continue", and "Review" buttons across multi-page forms, dynamically filling questionnaires until the final submit button is reached.
- **Proof-of-Submission**: Automatically captures high-resolution screenshots of submission confirmation pages and stores them in `data/screenshots/`.

### 6. Mission Control Web UI Dashboard
- **Dark-Mode First Design System ([`DESIGN.md`](DESIGN.md))**: Designed according to the `getdesign.md` and Google Stitch standards (Geist/Inter typography, deep `#07080a` canvas, hairline borders, and semantic color tokens).
- **Interactive Controls**: One-click action buttons to trigger discovery scans, batch evaluation, CV tailoring, dry-run applications, and full pipeline cycles.
- **Live Terminal & Metrics**: Real-time Bento metrics grid, 24h throughput bar, paginated jobs table with faceted search, and an embedded streaming console for `data/job_hunt.log`.

---

## Quick Start (< 5 Minutes)

### Prerequisites
- Python 3.14+
- `uv` package manager (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- Google Chrome or Chromium installed (`/usr/bin/google-chrome`)

### 1. Install Dependencies
```bash
uv sync
```

### 2. Configure Candidate Profile
Edit `config/candidate_profile.json` with your verified skills, experience bullets, and screening answers:
```json
{
  "full_name": "Abdulsamed Hamdy",
  "first_name": "Abdulsamed",
  "last_name": "Hamdy",
  "email": "abdulsamedhamdy@gmail.com",
  "location": "Cairo, Egypt",
  "open_to_remote": true,
  "citizenship": "Egypt",
  "authorized_countries": ["Egypt"],
  "sponsorship_required": false,
  "years_of_experience": 4,
  "verified_skills": ["Python", "FastAPI", "PostgreSQL", "Docker", "Kubernetes", "AWS", "Distributed Systems"]
}
```

### 3. Launch Mission Control Web UI
```bash
uv run python -m job_hunt.cli ui --port 8000
```
Open **`http://localhost:8000`** in your browser to access the full Mission Control dashboard.

### 4. Run 24/7 Autonomous Background Daemon
```bash
uv run python -m job_hunt.cli run --interval 3600
```

---

## Cloudflare Worker (24/7 free-tier operation)

The full pipeline also runs on Cloudflare Workers at
`https://jobhunt.habdulsamed777.workers.dev`, so the hunt keeps running when
the laptop is off. See [`worker/README.md`](worker/README.md) for internals.

```text
[hourly cron :15 + daily cron 02:00]
        │
        ▼  one source per queue message (≤50/batch)
[jobhunt-discover] ──► filter_recent + filter_remote ──► D1 upsert
        │                        (rich descriptions only)
        ▼
[jobhunt-evaluate] ──► deterministic score + gates
        │
        ▼  eligible only
[jobhunt-apply] ──► tailor text ──► pdf-lib PDF ──► R2 ──► ATS route
                     (greenhouse/lever POST | ashby dry-run | human queue)
```

**What runs where**

| Stage | Cloudflare Worker | Python / Actions |
|---|---|---|
| Fetch + parse all 319 sources | ✅ | ✅ (also covers the 84 python-only) |
| Deterministic evaluation | ✅ | ✅ |
| Tailored PDF via `pdf-lib` | ✅ | ✅ (master + visible skill line) |
| Direct ATS HTTP POST | ✅ per-board flags, OFF by default | ✅ |
| Playwright / Easy Apply / LinkedIn submit | ❌ no browser | ✅ human-approved only |
| CAPTCHA jobs | ❌ | ✅ human queue |
| Rezi mirroring + LLM-heavy work | ❌ | ✅ |

**HTTP API**

| Endpoint | Purpose |
|---|---|
| `GET /health` | Job count **plus profile sanity** (`profile_skills`, `live_apply`, `threshold`) — an empty `verified_skills` silently rejects every job |
| `GET /recent` | `actionable` list (tailored/submitted/human-queue) + `eligible` + `latest`. Read `actionable`, not `eligible`: qualifying jobs leave the `ELIGIBLE` state once tailored |
| `GET /coverage` | Measured coverage: configured vs attempted vs yielding sources, by kind |
| `GET /sources` | Configured Worker shard and hourly subset size |
| `POST /run?deep=1` | Fan out the **whole** shard (omit `deep` for the hourly subset) |
| `POST /probe` | Run one source read-only: `{"name":"stripe"}` → raw / fresh_24h / remote_fresh |

**Safety boundaries** — LinkedIn submits are never automated; non-ATS boards and
CAPTCHA jobs stay human-queued; live ATS POSTs stay `LIVE_APPLY=false` behind
per-board `APPROVE_*` flags.

---

## CLI Reference

| Command | Arguments | Description |
|---|---|---|
| `ui` | `--port 8000 --host 0.0.0.0` | Launch the Mission Control web dashboard |
| `status` | `--db data/jobs.db` | Print current pipeline state counts and 24h metrics |
| `llm-status` | `--llm-url http://127.0.0.1:4000/v1` | Check local LLM endpoint connectivity (optional; eval is deterministic by default) |
| `scan` | `--limit 50 --sources config/sources.yaml` | Run discovery across configured ATS sources |
| `evaluate` | `--limit 100 --threshold 70.0` | Run deterministic evaluation and scoring on discovered jobs |
| `tailor` | `--limit 50 [--use-rezi]` | Generate fact-checked CVs for eligible jobs (optional Rezi mirroring) |
| `apply` | `--limit 10 [--live]` | Launch browser automation (defaults to safe dry-run) |
| `run` | `--interval 3600 [--live]` | Start 24/7 continuous autonomous loop |
| `doctor` | `--db data/jobs.db` | Cold-start diagnostics (DB, adapters, secrets, source config) |
| `outcome` | `--job-id N --status applied\|interview\|offer\|rejected\|hired\|ghosted` | Record what actually happened to an application, for calibration |

---

## REST API Reference

The backend provides a REST API powering the Mission Control UI:

- **`GET /api/health`**: Real-time system health, database state, LLM connectivity, and active operations.
- **`GET /api/stats`**: Aggregate pipeline statistics, state breakdown, and 24h daily throughput.
- **`GET /api/jobs`**: Paginated jobs list supporting `state`, `source`, `min_score`, and `search` query params.
- **`GET /api/jobs/{id}`**: Complete job requisition details, AI evaluation breakdown, and audit trail.
- **`GET /api/cv/{id}`**: Download the ATS-stamped tailored PDF for a specific job.
- **`GET /api/screenshots/{filename}`**: View Playwright submission verification screenshots.
- **`GET /api/logs`**: Stream recent lines from `data/job_hunt.log`.
- **`POST /api/actions/scan`**: Trigger asynchronous discovery scan.
- **`POST /api/actions/evaluate`**: Trigger asynchronous batch evaluation.
- **`POST /api/actions/tailor`**: Trigger asynchronous CV tailoring.
- **`POST /api/actions/apply`**: Trigger browser automation applications (dry-run or live).
- **`POST /api/actions/cycle`**: Trigger a complete end-to-end pipeline cycle.

---

## Running Automated Tests

Run the complete suite across discovery, dedup, evaluation gates, CV tailoring,
browser automation, storage, and web endpoints. The Worker has its own suite:

```bash
uv run pytest tests/ -q -p no:cacheprovider
cd worker && npm test && npm run typecheck
```

```text
uv run pytest tests/ -q          # 209 passed, 1 skipped
cd worker && npm test            # 32 passed (vitest) + tsc --noEmit clean
```

---

## License

Internal proprietary software developed for autonomous career acceleration.
