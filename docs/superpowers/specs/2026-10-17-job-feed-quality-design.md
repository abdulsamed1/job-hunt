# Job feed quality: software roles only, true 24h, no duplicates, real coverage

**Date:** 2026-10-17
**Status:** draft for approval (builder-checked; all open questions resolved below)
**Scope:** discovery + evaluation on the Cloudflare Worker (the 24/7 runtime), parity in the Python
pipeline, and source-shard/config work.

## Intent and why

The owner looked at what the bot actually surfaced and saw three things it should never show:
sales/manager/team-lead roles, a feed dominated by crypto sources, and postings that are
duplicated or older than a day. The request: focus on software jobs, cover all sources rather than
a crypto-heavy few, never duplicate, and never show anything that is not posted within the last
24 hours.

Recon on 2026-10-17 (production D1 + code) shows each of those is a real defect, not a perception:

| Symptom | Cause | Evidence |
|---|---|---|
| Crypto-heavy feed | The hourly scout shard is hardcoded to 7 crypto orgs (coinbase, ripple, consensys, stellar, uniswap, opensea, alchemy) + 9 feeds, 4 of them crypto. It runs 24x/day and is what the owner sees. | `worker/scripts/gen_sources.py:30-36` |
| Breadth missing | 67 of 298 Worker sources yield anything: lever 2/54, ashby 15/64, greenhouse 35/133. Dead boards consume the free budget. | `GET /coverage` |
| Sales/manager roles applied | `scoreJob` has no role gate: score = skills + seniority + location. "Senior Manager, Application Security" and "Senior Salesforce Developer" both scored 100 and reached APPLICATION_STARTED. | `worker/src/lib/evaluate.ts:146-183`, `GET /recent` |
| Duplicates | Identity is the canonical URL hash only. The same posting from two boards, or two jids for one requisition, becomes two applications: D1 has (company,title) groups of x4 (Cognyte, jobgether) and x2 (Bending Spoons, Claroty, Elsight). | `worker/src/state.ts:28`; a `roleFingerprint` helper already exists in `worker/src/lib/hash.ts:33` and is imported but unused (`index.ts:22`) |
| Older than 24h | Python's JobSpy adapters are configured with `hours_old: 72` on 62 sources (bayt, naukri, indeed, freehire, bdjobs + 57 ATS orgs). The Worker separately hardcodes `filterRecentList(jobs, 12)` (`index.ts:239`) — its `hours_old` is never plumbed from config (`gen_sources.py` does not read or emit the field). Dateless postings pass both filters by design (`freshness.ts:26-30`). Live truth: 83 of 263 non-rejected D1 rows have no `posted_at`; 19 are genuinely within 24h. | `config/sources.yaml`, `worker/src/index.ts:239`, `worker/src/lib/freshness.ts:26-30` |

Greenhouse exposes no posting date; its adapter maps `updated_at` to `posted_at`
(`worker/src/adapters/ats.ts:19`, `src/job_hunt/discovery/adapters/greenhouse.py:72`). Greenhouse is
133 of 382 configured sources and the largest yielding family, so how "fresh" is judged for it
decides whether coverage survives this work. It is resolved in Decision 1.

The Python pipeline already has role patterns (`SWE_ROLE_PATTERNS`, `NEGATIVE_ROLE_PATTERNS`,
`src/job_hunt/evaluation/engine.py:15-52`) that reject every title class the owner complained about
except people-management titles containing an engineering substring ("Director of Software
Engineering - Distributed Systems" passes on `distributed systems`). The Worker has none of it.
The Worker is the 24/7 runtime, so that is where the gate belongs; Python gets parity.

## Goals

1. Only software-engineering roles are surfaced or applied to. No sales, BD, marketing,
   people-management, or CRM-consulting roles, ever.
2. Every surfaced posting is within the last 24 hours by the best date its source provides, and
   dateless postings never become eligible.
3. One requisition produces at most one application and at most one notification, no matter how many
   boards carry it.
4. The feed reads as "all software jobs": industry-neutral sources, hourly shard rebuilt around
   measured yield, dead boards removed from the budget.
5. Honest reporting: `/coverage` states live/dead per source, and distinguishes dated freshness from
   proxy freshness, so coverage cannot silently rot again.

## Non-goals

- Re-tuning the skills/seniority/location scoring that produces the 0-100 score beyond what the role
  gate and unknown-title cap need.
- Re-enabling LinkedIn (account ban; explicitly out of scope until the owner says otherwise).
- Any new paid API, proxy, or captcha-solving dependency.
- Multi-variant resumes, R2 deletion, or changes to the apply/notification state machine beyond what
  these fixes require.

## Anti-goals (a failure even if it technically works)

- The feed becomes crypto-only or dominated by any single industry again.
- A manager, team-lead, sales, or CRM-consultancy role reaches the owner's Telegram, in any state.
- The same requisition produces two applications or two notifications.
- A dateless posting, or one older than 24h by its source's own date, reaches ELIGIBLE.
- Coverage numbers are asserted rather than measured.

## Decisions taken (my calls; the owner delegated these with "proceed")

1. **Greenhouse freshness = `updated_at` within 24h, labelled as a proxy.** Fetching a real posting
   date per job costs a subrequest against the 50 cap for the largest source family, and Greenhouse
   simply does not publish one. A requisition edited within 24h is an actively maintained posting.
   `/coverage` reports these separately as `proxy_fresh` so the number is never confused with a true
   posting date. A requisition untouched for 24h+ drops.
2. **The role gate runs at discovery, not only at evaluate.** `SEEKING` is emitted at discovery for
   every posting (`index.ts:262`), so gating only in `scoreJob` would still put "Senior Manager" in
   the owner's Telegram. `roles.ts` runs pre-upsert in the discover path (emitting `FILTERED` with
   the matched rule) and again in `scoreJob` as defense in depth. Classification is pure CPU — no
   extra I/O.
3. **Unknown titles are accepted for discovery but capped below the eligibility threshold.** A hard
   positive list alone drops legitimate titles ("Android Engineer", "Firmware Engineer"). Unknown
   titles are stored and visible as `FILTERED`/`unknown`, scored with a cap of 69 against a threshold
   of 70, so they are never applied to. Python keeps its stricter behaviour (requires a positive SWE
   match) and gains the new negatives; the two runtimes are allowed to differ here because only the
   Worker applies autonomously.
4. **Dateless postings never become eligible on the Worker.** The allow-list the builder-check
   proposed (bayt/naukri/ziprecruiter) is Python-only and never reaches the Worker, and the Worker's
   indeed adapter sends no recency parameter — so under a strict reading no Worker source qualifies.
   That is the intended rule: dateless rows are stored for the record, counted as
   `dateless_dropped`, and never scored. Python's server-side-filtered boards (bayt, naukri,
   ziprecruiter, indeed, bdjobs) keep their filtered path, with the 72h override clamped to 24h.
5. **Dedupe is a strict fingerprint; no description escape hatch.** A "descriptions differ
   materially" exception re-leaks exactly the case being fixed (the x4 groups come from different
   boards with different descriptions). Identity is
   `sha256(company | normalized title | normalized location)`. Title: lowercase, strip
   tracking/emoji, collapse punctuation and whitespace. Location: lowercase, map remote synonyms
   (remote, anywhere, worldwide, wfh, distributed, "united states", "us", "global") to a single
   `remote` token; empty/unknown maps to `?` so an unknown location never merges with a real one.
   Two requisitions with the same title, same company, and same location are treated as one — the
   first seen wins, the later is `DUPLICATE`.
6. **Duplicate checks are batched.** One `SELECT role_fingerprint ... WHERE role_fingerprint IN (...)`
   per source invocation, not a query per posting, against the 50-query budget.
7. **D1 migration is additive and forward-applied.** Add `role_fingerprint` + index; backfill only
   rows in non-terminal states (263 rows today). Terminal rows stay as history. Consequence, stated
   plainly: duplicate detection is exact for new rows; already-stored duplicates are not retroactively
   merged, which is why Decision 9 exists.
8. **Existing not-yet-applied violations are demoted, not erased.** Already-submitted applications
   are real and stay. Rows in `ELIGIBLE`/`TAILORED` that violate the new role or freshness rules are
   moved to `PRE_FILTERED_OUT` with a reason by a one-off backfill, so `/recent` stops showing them.
   Requires permitting that transition in `VALID_TRANSITIONS`.
9. **Role rejects use the existing `PRE_FILTERED_OUT` bucket; duplicates use the existing
   `DUPLICATE` state.** No new state enum, so no existing query or Python transition table breaks.
   Counters live on `source_health` plus `/health` aggregates.
10. **The CRM toggle is a Worker var, not a profile field.** `REJECT_CRM_ROLES` in `wrangler.toml`
    (default `"true"`) avoids rotating the `PROFILE_JSON` secret and keeps `profileFromEnv`'s fixed
    field list untouched.
11. **Yield ranking uses a rolling window.** `source_health` currently counts lifetime yields and
    stamps `last_seen` on every attempt including zero-yield ones, so a lifetime ranking re-promotes
    boards that died weeks ago. Ranking becomes `yields / attempts` over a 7-day window with a
    minimum-attempts floor, computed by the probe script, and written into config.
12. **Industry is an explicit field, not inferred.** `config/sources.yaml` gains
    `industry: crypto | software | fintech | general` per source, so the shard's crypto cap is a
    declared fact rather than a regex guess. The hourly shard = highest-yielding ~25 sources with at
    most 2 crypto orgs and 3 crypto feeds, and must include the non-crypto software boards and the
    Python-only filtered boards' Worker equivalents.
13. **The dead-board list becomes a real artifact.** `data/dead_boards.json` is a 15-entry stub keyed
    `kind:org`, which cannot key feeds (D1 uses bare `def.name`). The probe writes a uniform
    `name`-keyed ledger with attempts, last-yield time, and verdict, and `config/sources.yaml` gains
    `status: live | dead | unknown` that the generator honours.

## The work

### 1. Role gate

New `worker/src/lib/roles.ts` exporting `classifyRole(title): { verdict: "software" | "reject" |
"unknown", rule?: string }`, used by both the discover path and `scoreJob`:

- **reject (people-management):** `manager`, `team lead`, `group lead`, `head of`, `director`, `vp`,
  `chief`, `supervisor`, `engineering manager`, `delivery manager`. IC ladder allowed first:
  `lead engineer`, `lead software engineer`, `staff`, `principal`, `senior`.
- **reject (revenue/BD):** `sales`, `business development`, `account executive`, `account manager`,
  `sales development`, `marketing`, `partner(ships)`, `customer success`, `customer support`.
- **reject (CRM/platform administration, gated by `REJECT_CRM_ROLES`):** `salesforce
  consultant|architect|administrator|analyst|developer` and the same for other CRM names.
- **reject (non-engineering):** `recruiter`, `talent acquisition`, `human resources`, `accountant`,
  `finance`, `legal`, `nurse`, `medical`, `driver`, `content writer`, `graphic designer`, `intern`.
- **software (accept):** the existing `SWE_ROLE_PATTERNS` list, plus `mobile|ios|android`, `qa|sdet|
  test engineer`, `security engineer`, `embedded|firmware`, `forward deployed engineer`, and language
  titles the profile targets (`nodejs|typescript|react|reactjs|javascript|python|golang|rust|java`
  combined with engineer/developer).
- **unknown:** everything else; capped below threshold (Decision 3).

Rejections carry the matched rule into the job's reasoning so Telegram and `/recent` can say why.
Python parity: add the leadership/BD/CRM patterns to `NEGATIVE_ROLE_PATTERNS` and reorder
`pre_filter` so the positive IC allow-list is evaluated before the negatives (today negatives run
first, at `engine.py:369`, which cannot express "lead engineer is fine, team lead is not").

### 2. Freshness

- Worker: the window becomes the single named constant `HOURS_OLD = 24`. Not the current hardcoded
  12h: with a daily deep sweep, a 12h window silently drops every posting that lands 13-24h before the
  sweep, and the owner's requirement is 24h. Add the freshness re-check to the apply path — today the
  apply path re-checks only the disability gate (`index.ts:312`); the dead `freshWithin` helper in
  `pipeline.ts:158` is removed or reused, not left dangling.
- Python: clamp the 62 `hours_old: 72` entries to 24. `hours_old: 0` stays available for a deliberate
  manual run, never for a scheduled one (the CLI and the Actions workflow own that default).
- Dateless (Decision 4): stored, counted, never scored.
- Per-source daily intake cap (default 30) applied after freshness, keeping the highest-scoring
  remainder rather than the first N, so one chaty board cannot flood a day.

### 3. Duplicate suppression

- Reuse `roleFingerprint` from `worker/src/lib/hash.ts:33` (already written, never called) and wire
  it into `upsertJob` with Decision 5's normalization.
- Batch lookup per source invocation (Decision 6); a fingerprint hit with a different canonical hash
  writes state `DUPLICATE`, never scored, never notified, never applied.
- D1: `ALTER TABLE jobs ADD COLUMN role_fingerprint TEXT` + index in `schema.sql`, applied through
  the same migration pattern the repo uses, backfilling non-terminal rows (Decision 7).
- Strip `utm_*` and friends before the canonical hash so aggregator mirrors collapse.
- Python already stores and looks up `role_fingerprint` (`storage.py:210-213`); align its
  normalization with Decision 5 so both runtimes agree.

### 4. Coverage

- Probe all 382 sources with the product's own adapters; write the uniform dead/live ledger
  (Decision 13); regenerate `sources.generated.ts`; honour `status` in scheduled shards.
- Rebuild the hourly shard on measured 7-day yield rate with the industry caps (Decisions 11-12),
  replacing the hand-maintained `HOURLY_ORGS`/`HOURLY_FEEDS` crypto lists.
- Broaden queries on every JobSpy-style source to backend, full stack, software, reactjs, nodejs,
  typescript, javascript, forward deployed engineer; fix the `Frontier Deployed Engineerk` typo in
  `naukri-india-remote`.
- Add non-crypto remote-first employers that probe live. Every added source must prove yield before
  it stays: local `scan_source` count > 0 and `POST /probe` showing `remote_fresh > 0`.
- `/coverage` reports configured, attempted, yielding, live-software, `dated_fresh`, `proxy_fresh`,
  `dateless_dropped`, `duplicate_suppressed`, so "how many sources really work" is one number.

### 5. Notifications

Telegram messages carry source and posted age ("2h ago", "18h ago") beside the title, and
role-rejected postings never produce `SEEKING`. A feed that drifts older than a day becomes visible
at a glance instead of weeks later.

### 6. One-off backfill (Decision 8)

A single script demotes existing `ELIGIBLE`/`TAILORED` rows that violate the role or freshness rules
to `PRE_FILTERED_OUT` with a reason, after `VALID_TRANSITIONS` permits it. Run once, dry-run output
first, submitted rows untouched.

## Constraints

- No new npm or Python dependencies.
- Worker free tier: 5 cron triggers, 50 subrequests/queries per invocation, queue batch caps. The
  role gate is pure CPU; the fingerprint check is one batched query per source invocation.
- Zero paid APIs. Probing uses the product's own adapters.
- Every change needs tests; `uv run pytest`, `worker npm test`, `npx tsc --noEmit` stay green.
- Safety flags unchanged: `LIVE_APPLY=false`, `LINKEDIN_ENABLED=false`. New companies are dry-run
  first, as today.

## Left to the builder

- Exact placement of `classifyRole` calls in the discover handler and the reasoning strings.
- Where the intake cap lives: Worker discover stage vs. generated source definitions.
- The probe script's shape and how `status`/`industry` land in `config/sources.yaml`.
- Test fixtures per rejected title class, drawn from the real leaks: Wizeline "Senior Salesforce
  Developer", Consensys "Senior Manager, Application Security", Elastic "Director of Software
  Engineering - Distributed Systems", TokyoDev "Deputy Department Manager".

## Verification before this is called done

1. Over a full day, `/recent` and Telegram show zero manager/team-lead/sales/CRM titles in any state.
2. D1 has no `(company, normalized title, normalized location)` group above 1 among non-terminal
   rows discovered after deploy.
3. Every actionable row is either `dated_fresh` within 24h or `proxy_fresh` within 24h; the
   `dateless_dropped` counter is non-zero and reported.
4. `/coverage` shows software roles from at least 10 distinct non-crypto companies in one day, with
   the hourly shard containing no more than 2 crypto orgs.
5. Both suites green; a dry-run audit shows one POST per requisition; the one-off backfill is dry-run
   reviewed before it runs.
