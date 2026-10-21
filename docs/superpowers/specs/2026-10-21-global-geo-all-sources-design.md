# Design: global remote + Egypt local, every live source, LinkedIn discovery only

Date: 2026-10-21
Owner decision: the candidate is based in Egypt and wants

- **remote roles anywhere in the world**, and
- **on-site / hybrid roles in Egypt**, and
- **remote roles in Egypt** (already covered by "remote anywhere"),
- **every job the configured sources actually publish**, not a capped sample,
- **LinkedIn job search** (discovery) with **no automated application through the
  owner's LinkedIn account**, to protect the account.

## 1. Geography policy

A posting is **in scope** when any of these holds:

1. it signals remote (location or body: remote, worldwide, anywhere, WFH,
   distributed, telecommute, virtual), or
2. its location names Egypt (country or a major Egyptian city), or
3. its location is unknown/blank (fail-open, as today — a board that gives no
   location never loses a posting).

Everything else — a placed on-site posting outside Egypt — is **out of scope**
for the feed. This is the one rule both runtimes implement.

- Dateless postings keep today's rule: stored for the record, never scored,
  never notified as SEEKING, never applied to.
- Scoring does not change: remote still scores higher on location, but an
  in-scope Egypt on-site role is scored and eligible on its merits.

Both runtimes get the same helper and the same city list:

- Python: `job_hunt.discovery.freshness.filter_target_geo`
- Worker: `lib/freshness.ts` `filterTargetGeoList`

`--remote-only` (strict remote-only) stays available as an explicit dial; the
scheduled sweep uses the new `--target-geo`.

## 2. Coverage of every source

1. **No arbitrary per-source cap.** The Worker's `INTAKE_CAP = 30` becomes a
   configurable safety bound (`INTAKE_CAP` var, default 150) so a normal board
   contributes everything it published; `intake_capped` remains the visibility
   counter.
2. **Deep sweep every 6 hours** instead of once a day. With a 24h window, a
   daily sweep can miss postings just before the sweep time; a 6h cadence
   guarantees every dated posting is seen while still inside the window.
3. **Muted events cost no write.** A stage event that the Telegram allowlist
   mutes returns `"skipped"` before the ledger insert: there is nothing to
   deduplicate when nothing is sent, and emission is already first-time-only
   (`isNew` / `outcome.recorded`). This keeps the intake expansion from paying
   one D1 write per posting for notifications nobody receives.
4. **Dead boards stay out.** A 404/410 board has no jobs to catch; the source
   probe is the only thing that can revive one. Re-probe before trusting the
   count.

## 3. LinkedIn discovery only

- **Discovery is the public guest endpoint** (`jobs-guest/...`): no cookies, no
  login, no account. This cannot get the owner's account banned because the
  account is never used.
- Two search sources: **worldwide remote** (`geo_id 92000000`) and **Egypt**
  (`geo_id 106155005`), same role queries. The Worker guest adapter loops over
  every configured location instead of only the first.
- LinkedIn enters the hourly/deep shard only on its own cadence (6h), not every
  hourly run, to stay polite to the guest endpoint.
- **No automated application, ever:**
  - Worker `tailor-apply`: a LinkedIn row is saved as `APPLICATION_STARTED`
    with `"linkedin: discovery only — apply manually"` and never reaches an ATS
    client.
  - Python `run_application_stage`: LinkedIn rows are filtered out
    unconditionally (not only while the feature was disabled).
  - The browser engine keeps its approval gate as the third layer.
- `linkedin_require_approval: true` and `LINKEDIN_ENABLED` wiring stay; the
  flag now means "discovery is on", not "apply is on".

## Non-goals

- No LinkedIn Easy Apply automation, no logged-in LinkedIn scraping, no
  LinkedIn API credentials.
- No change to the 24h recency window or the software-only role gate.
- The 176 dead boards are not "fixed" by code; only a probe can revive one.

## Acceptance

- A Cairo on-site posting passes discovery; a Bangalore on-site posting does not.
- A board's 100 fresh postings all reach the store (subject to the 150 bound).
- LinkedIn guest postings appear in the feed; none is ever sent to an ATS apply
  client, and the Python application stage never queues one.
- Both suites green; deployed Worker verified with `scripts/verify_deploy.py`.

## Implementation deltas (recorded after building it)

- **Robots.** `linkedin.com/robots.txt` ends with `User-agent: *` /
  `Disallow: /`. The Python registry therefore skips LinkedIn (its robots gate
  is unchanged), so the CI/local sweep never fetches it and the Worker guest
  fetcher is the only LinkedIn intake. This is an owner-accepted trade-off, not
  an oversight: the guest endpoint touches no account, so no account can be
  banned. The probe now raises `RobotsDisallowed` for a refused source and
  records it as *not measured* (flaky) — previously a robots skip looked
  identical to a board answering with nothing, and the two LinkedIn sources
  were recorded `dead`.
- **Probe liveness follows the shipped geography policy.** "Live" now means at
  least one *usable* posting at any age (remote anywhere, placed in Egypt, or
  unknown location) instead of strict remote-only. Without that change an Egypt
  on-site board would be called dead and excluded from the shard forever;
  `bayt-egypt-tech` was revived by exactly this fix.
- **Remote search signal.** A remote-filtered LinkedIn search (`f_WT`, the
  worldwide source) prints the employer's city on each card. The card is now
  tagged `"<city> (Remote)"` so the target-geo filter sees the signal the search
  already proved; a geo search (Egypt) leaves the location untouched. The
  Python adapter's `remote_flag` metadata was inverted (`not remote_only`) and
  is fixed.
- **Failure is not emptiness.** Both guest adapters now raise when every request
  fails (429/403/5xx/network) instead of returning `[]`; the Worker discover
  stage fails the source only when *all* queries fail, so a partial harvest
  still lands. This is what keeps a throttled source from being recorded as a
  quiet board (and from being called dead by the probe).
- **Volume.** `max_pages_per_query` is 1 for both LinkedIn sources (9–10 requests
  per source per 6h run). Two pages doubled the guest load for no extra
  coverage — `target_jobs_count` was already reached on page one.
- **Third safety layer is unconditional.** The browser engine refuses any
  LinkedIn posting before launching a browser, independent of
  `linkedin_approved` / `LINKEDIN_ENABLED`; the spec's "keeps its approval
  gate" wording is superseded by the refusal.
- **Muted Telegram events no longer claim the ledger key** (no D1 write). Each
  caller emits only on a first-time transition, so re-enabling an event cannot
  replay old ones, and a chatty muted stage stops costing one write per posting.
