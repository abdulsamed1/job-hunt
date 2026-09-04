# Low-Cost Autonomous Job-Application Agent for Software Engineers

A continuous, modular, 24/7 autonomous job discovery, evaluation, CV tailoring, and browser-automated application system with **$0 operating cost** designed for Software Engineer roles.

## Architecture Overview

```
[Discovery (~210 Sources)]
        │
        ▼
[Normalization & Multi-Signal Deduplication] (RFC 3986 URL hash + Role fingerprint + Content hash)
        │
        ├── Duplicate? ──► [DUPLICATE] (Record Provenance, Stop)
        │
        ▼
[Deterministic Pre-Filter] (Regex SWE Role Match, Negative Filter, Clearance Check)
        │
        ├── Non-SWE? ──► [PRE_FILTERED_OUT] (0 AI Tokens, Stop)
        │
        ▼
[Structured Evaluation] (Tech Stack Alignment, Seniority, Location Match)
        │
        ├── Score < 70%? ──► [REJECTED] (Audit Log, Stop)
        │
        ▼
[Fact-Preserving Tailored CV] (Strict Anti-Hallucination Gate against verified Candidate Profile)
        │
        ▼
[Headless Browser Automation] (Playwright + Google Chrome / Chromium)
        │
        ├── CAPTCHA / Bot Challenge? ──► [BLOCKED_CAPTCHA] (Screenshot Saved, Human Alert)
        │
        ▼
[Autonomous Submission & Verification] ──► [SUBMITTED] (DOM/URL Confirmation + Audit Trail)
```

## Key Capabilities

1. **200+ Source Discovery ($0 Cost)**:
   - Configured in `config/sources.yaml` with 210 top tech companies and remote boards.
   - Modular adapters for **Greenhouse**, **Lever**, **Ashby**, **Workday**, **SmartRecruiters**, and **Remote Feeds**.
   - Direct public API queries over lightweight HTTP (`httpx`), avoiding heavy and brittle DOM scraping.

2. **Persistent Multi-Signal Deduplication**:
   - RFC 3986 §6 canonical URL normalization (stripping campaign/tracking params like `utm_*`, `gh_src`, etc.).
   - Role identity fingerprinting combining normalized company legal forms (`Stripe, Inc.` -> `stripe`), title synonyms, and location.
   - SHA-256 content hashing.
   - Provenance tracking preserves every source where a job was seen without re-processing.

3. **Zero-Token Deterministic Pre-Filtering**:
   - Immediate positive match for Software Engineer, Backend, Frontend, Full-Stack, Systems, SRE, Platform, Distributed Systems.
   - Immediate rejection of non-SWE roles (Marketing, Sales, HR, Legal, Healthcare, unpaid internships) before invoking AI.

4. **100% Fact-Checked Tailored CV Generation**:
   - Master truth strictly anchored in verified `CandidateProfile`.
   - Reorders and highlights verified bullets based on job relevance.
   - Strict AST fact-verification gate prevents any hallucinated metrics, employers, dates, or technologies. Automatically falls back to verified Master CV on any anomaly.

5. **Headless Browser Application Engine**:
   - Built on Playwright controlling headless Chrome (`/usr/bin/google-chrome`).
   - Handles standard form fields, screening questions, and file attachment (`input[type="file"]`).
   - Bot challenge & CAPTCHA detection: captures screenshot, flags `BLOCKED_CAPTCHA`, and prevents infinite loops.
   - Safe dry-run mode enabled by default.

6. **SQLite State Machine & Stuck-Job Recovery**:
   - Write-Ahead Logging (WAL) for high concurrency and crash resilience.
   - Automated stuck-job recovery: reclaims applications interrupted by process crashes (>15m) with bounded retries (max 3).
   - Complete audit trail table recording every transition and error.

## CLI Usage

### Check Tracker Status
```bash
uv run python -m job_hunt.cli status
```

### Scan Sources (Discovery)
```bash
# Scan first 5 sources
uv run python -m job_hunt.cli scan --limit 5

# Scan all 210 configured sources
uv run python -m job_hunt.cli scan
```

### Evaluate Discovered Jobs
```bash
uv run python -m job_hunt.cli evaluate --threshold 70.0
```

### Tailor CVs for Eligible Jobs
```bash
uv run python -m job_hunt.cli tailor
```

### Run Application Automation (Dry-Run / Live)
```bash
# Dry run (safe inspection with screenshots)
uv run python -m job_hunt.cli apply --limit 5

# Live autonomous submission
uv run python -m job_hunt.cli apply --limit 5 --live
```

### Run Continuous 24/7 Loop
```bash
uv run python -m job_hunt.cli run --interval 3600
```

## Running the Test Suite

```bash
uv run pytest -v
```
