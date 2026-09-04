# Autonomous Job Application Agent for Software Engineers

An enterprise-grade, 24/7 autonomous job discovery, evaluation, CV tailoring, and browser-automated submission engine operating at **$0 marginal cost** for Senior Backend, Distributed Systems, and Infrastructure Software Engineers.

---

## High-Level Architecture

```
                                 [298 Active Sources & Job Boards]
                    (LinkedIn, Greenhouse, Ashby, Lever, SmartRecruiters, Workable, BambooHR)
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
                                                        [FreeLLM Structured AI Eval]
                                                       (Skills alignment, seniority, location)
                                                                      │
                          ┌───────────────────────────────────────────┴───────────────────────┐
                          ▼                                                                   ▼
                [Fit Score < 70%]                                                    [Fit Score >= 70%]
             (State: REJECTED, Stop)                                                 (State: ELIGIBLE)
                                                                                              │
                                                                                              ▼
                                                                                  [Fact-Preserving CV Stamping]
                                                                                (Master PDF + 1pt white ATS text)
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

### 1. High-Throughput Multi-ATS Discovery (298 Active Sources)
- **Extensive ATS Coverage**: Native adapters for **LinkedIn**, **Greenhouse**, **Ashby**, **Lever**, **SmartRecruiters**, **Workday**, **Workable**, **BambooHR**, and **RSS/XML feeds**.
- **Dedicated LinkedIn Guest Scraper**: Direct pagination against public guest search endpoints (`seeMoreJobPostings/search`) with automated backoff, anti-429 rotation, and zero login credentials required. Yields 500+ fresh postings per run.
- **Daily 500+ Target Tracking**: Real-time velocity tracking ensuring a minimum of 500 unique engineering requisitions are ingested and analyzed every 24 hours.

### 2. Multi-Signal Deduplication & Provenance
- **Canonical URL Normalization**: Strips RFC 3986 tracking parameters (`utm_*`, `gh_src`, `ref`, `source`, `fbclid`).
- **Role Identity Fingerprinting**: Normalizes corporate legal variants (`Stripe, Inc.` $\to$ `stripe`), cleans title synonyms, and hashes normalized job location.
- **Provenance Ledger**: Retains full cross-channel discovery history without re-processing duplicates.

### 3. Zero-Token Liveness & Repost Detection
- **Liveness Gate**: Performs fast HTTP HEAD/GET checks to immediately filter out 404s, expired postings, and filled jobs before spending LLM tokens.
- **Stale Job / Repost Detector**: Detects recycled requisitions across dates and avoids applying to expired reposts.

### 4. Rezi-Compliant ATS Stealth Injection
- **Single Source of Truth**: Candidate master PDF (`Abdulsamed_Hamdy.pdf`) is strictly preserved without visual destruction, layout shifting, or text refactoring.
- **ATS White-Text Stamping**: Automatically converts the complete job description into single-line 1pt invisible white text stamped onto the final page via PyMuPDF. Guarantees maximum keyword matching in automated ATS parsers while maintaining human readability.
- **Metadata Compliance**: Injects standardized document metadata titles (`Candidate Name - Target Role - Resume`) to pass Rezi AI parsing audits with scores $\ge 88/100$.

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
  "email": "abdalsamed71@gmail.com",
  "location": "Cairo, Egypt",
  "open_to_remote": true,
  "work_authorization": "Authorized to work in Egypt, Remote Worldwide",
  "sponsorship_required": false,
  "years_of_experience": 6,
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

## CLI Reference

| Command | Arguments | Description |
|---|---|---|
| `ui` | `--port 8000 --host 0.0.0.0` | Launch the Mission Control web dashboard |
| `status` | `--db data/jobs.db` | Print current pipeline state counts and 24h metrics |
| `llm-status` | `--llm-url http://127.0.0.1:4000/v1` | Check FreeLLMAPI connectivity and health |
| `scan` | `--limit 50 --sources config/sources.yaml` | Run discovery across configured ATS sources |
| `evaluate` | `--limit 100 --threshold 70.0` | Run AI evaluation and scoring on discovered jobs |
| `tailor` | `--limit 50` | Generate fact-checked, ATS-stamped CVs for eligible jobs |
| `apply` | `--limit 10 [--live]` | Launch browser automation (defaults to safe dry-run) |
| `run` | `--interval 3600 [--live]` | Start 24/7 continuous autonomous loop |

---

## REST API Reference

The backend provides a REST API powering the Mission Control UI:

- **`GET /api/health`**: Real-time system health, database state, FreeLLMAPI status, and active operations.
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

Run the complete test suite across discovery, normalization, ATS tailoring, browser automation, and web endpoints:

```bash
uv run pytest -v
```

```text
tests/test_browser_automation.py ...  [  4%]
tests/test_cv_tailor.py .......       [ 16%]
tests/test_dedup.py .......           [ 27%]
tests/test_discovery.py .......       [ 38%]
tests/test_evaluation.py ....         [ 45%]
tests/test_liveness.py .....          [ 53%]
tests/test_llm.py .....               [ 61%]
tests/test_new_adapters.py ....       [ 67%]
tests/test_orchestrator.py .....      [ 75%]
tests/test_questionnaire.py ..        [ 79%]
tests/test_reposts.py ...             [ 83%]
tests/test_storage.py ...             [ 88%]
tests/test_web_ui.py .......          [100%]
======================= 62 passed in 12.76s =======================
```

---

## License

Internal proprietary software developed for autonomous career acceleration.
