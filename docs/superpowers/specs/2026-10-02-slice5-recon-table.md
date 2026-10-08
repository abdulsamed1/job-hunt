# Slice 5 recon — go/no-go table for world-coverage candidates

Date: 2026-10-07 · Task 1 of Slice 5 · probe script: `scripts/recon_boards.py`
Method: polite triage (≤6 requests per board family, 20s timeouts, browser UA,
200KB read caps, ~1s pauses). All URLs below are unauthenticated by
construction — a 401/403/406 is an auth/bot wall, i.e. evidence for NO-GO.
Endpoint shapes cross-checked against
`.ref-repos/career-ops/providers/{teamtailor,recruitee,personio,pinpoint,icims}.mjs`
and the `jobindex-search` / `jobbank-search` SKILL + url-reference files
(read-only reference, no code copied).

GO bar: HTTP 200 + job array + stable ID field + no auth.
Scope bound (per brief): candidates probed in priority order; **the first 3
GOs proceed to Task 2**. Later GO evidence is recorded but held out of scope.

## Verdicts

| # | Board | Endpoint probed (exact URL) | Auth? | Jobs returned | Stable IDs? | Verdict + reason |
|---|-------|-----------------------------|-------|---------------|-------------|------------------|
| 1 | Teamtailor | `https://api.teamtailor.com/v1/jobs?page%5Bsize%5D=5` → **406**, `{"errors":…}` | Yes (token) | 0 | — | **NO-GO** — global API is token-walled; per-tenant feeds are the path |
| 1 | Teamtailor | `https://career.teamtailor.com/jobs.rss` → **200** `application/rss+xml` | No | **14** `<item>` | `link` (per-job URL) | **GO** ✅ — public per-tenant RSS, no auth |
| 1 | Teamtailor | `https://softwarefinder.na.teamtailor.com/jobs.rss` → **200** | No | **19** `<item>` | `link` | **GO** ✅ (2nd tenant confirms shape; note: multi-level subdomain `softwarefinder.na` exists — adapter host-regex `^<slug>\.teamtailor\.com$` would reject it, needs loosening) |
| 2 | Recruitee | `https://make.recruitee.com/api/offers/` → **200** `application/json` | No | **2** `offers[]` | `id` (+`careers_url`) | **GO** ✅ — public per-tenant offers API, numeric `id`, full description embedded |
| 2 | Recruitee | `https://happeo.recruitee.com/api/offers/` → **200** | No | **1** `offers[]` | `id` | **GO** ✅ (2nd tenant, same shape) |
| 2 | Recruitee | `https://radix.recruitee.com/api/offers/` → **200** | No | **4** `offers[]` | `id` | **GO** ✅ (3rd tenant, same shape) |
| 3 | Personio | `https://vivid.jobs.personio.de/xml` → **200** `text/xml` | No | **21** `<position>` | `id` (numeric, e.g. `2452785`) | **GO** ✅ — public per-tenant XML feed. Caveat: feed embeds raw markup inside `<jobDescriptions>` that breaks naive whole-doc XML parsing (hit `unclosed CDATA` on first attempt); must strip description subtrees and split `<position>` blocks exactly as the ref provider does. Count note: 21 here (2026-10-07 probe) vs 24 at registration — the board grew between probes, not a discrepancy |
| 3 | Personio | `https://finn.jobs.personio.de/xml` → **200**, 72-byte empty `<workzag-jobs/>` | No | 0 | — | Tenant resolves but publishes zero positions — endpoint shape valid, board empty (supports GO above, not a counter-signal) |
| 4 | Pinpoint | `https://workwithus.pinpointhq.com/postings.json` → **200** `application/json` | No | **3** `data[]` | `id` (posting UUID in `url`/`path`, e.g. `/en/postings/<uuid>`) | **GO on evidence, HELD OUT** — probed in-scope (only 2 GOs banked at probe time) but 4th in priority order, so out of Task 2 scope after the 3-board bound |
| 4 | Pinpoint | `https://clearbank.pinpointhq.com/postings.json` → **404** (empty body) | — | — | — | Bad tenant guess, not a board signal (ClearBank is not a Pinpoint tenant) |
| 5 | iCIMS | `careers-centricbrands.icims.com/jobs/search` → **200** (43KB job-search page); `careers-nv5.icims.com/jobs/search` → **200** (23KB, "Job Listings at NV5") | No (public search pages) | Unproven (HTML search pages, no clean JSON array observed) | **DEFERRED** — confirmed tenants resolve with live listings, but extraction needs an HTML/JS path (no JSON array); out of scope after the 3-GO bound. Earlier `careers-fedex/careers-ibm` guesses → 404 (bad slugs, not board signals) |
| 6 | JobIndex | `https://www.jobindex.dk/jobsoegning?q=python&jobage=7` → **200** `text/html` | No | **14** results w/ `tid` | `tid` (e.g. `h1647303`, canonical `/jobannonce/<tid>`) | **GO on evidence, HELD OUT** — `var Stash = {...}` hydrated state parses (jobsearch/result_app → searchResponse → results[]); `/jobsoegning.json` is dead (204) so HTML-scrape-of-Stash is the technique (validates T3). Out of Task 2 scope after the 3-board bound |
| 7 | Jobbank.dk | `https://jobbank.dk/job/rss?key=python` → **403** (single attempt, minimal headers) | Bot wall | 0 | — | **NO-GO (provisional)** — Cloudflare bot protection blocks plain-Python fetch, consistent with the skill docs' warning; the skill CLI path may work but was not retried post-bound. Re-probe candidate if a DK slot opens |

Wrong-guess log (no board signal, recorded so nobody re-probes these):
`einride/northvolt.teamtailor.com`, `hotjar/getyourguide/careem.recruitee.com`,
`tier/hellofresh.jobs.personio.de`, `monzo.pinpointhq.com`,
`careers-fedex/careers-ibm.icims.com` → 404 (bad slugs, not dead boards); `careers-centricbrands/careers-nv5.icims.com` → 200 live search pages (controller probe 2026-10-07, extraction path still unproven).

## Proceeding to Task 2 (3 boards)

1. **Teamtailor** — per-tenant `/jobs.rss` (RSS items, `link` as ID; watch multi-level subdomains).
2. **Recruitee** — per-tenant `/api/offers/` (`offers[]`, numeric `id`, description embedded).
3. **Personio** — per-tenant `/xml` (`<position>`, numeric `<id>`; strip `<jobDescriptions>` before parse; `?language=en` available; HTML `job-box` fallback exists per ref provider).

## Requests-per-board audit (politeness)

teamtailor 6 · recruitee 6 · personio 6 · pinpoint 4 · icims 4 · jobindex 1 · jobbank 1 — all single-digit.

## Employer-identity check (final fix wave, 2026-10-08 — one polite homepage GET each, browser UA, 20s timeout)

- `recruitee-radix`: `https://radix.recruitee.com/` → **302** to `https://superlinear.recruitee.com/` → **200**, `<title>` "Job openings⎥Superlinear", meta "Explore open roles at Superlinear and work on mission-critical AI…"; 4 offer links, all on-tenant internal roles (Demand Generation Lead, Enterprise Account Executive, Product Marketing Lead, Solutions Sales Engineer); offers carry `company_name: Superlinear`. **Single employer (tenant alias, not an agency) — KEEP.**
- `teamtailor-softwarefinder-na`: `https://softwarefinder.na.teamtailor.com/` → **200**, `<title>` "Join Our Team! - Software Finder"; 5 job links, all on-tenant internal roles (Assistant Manager Finance, Sales Development Representative, Senior PMO Specialist, Client Success Specialist, Assistant Manager Demand Generation). **Single employer — KEEP.**
- Neither board dropped, so all counts (325 configured / 241 Worker-runnable + 84 python-only) stand unchanged.
