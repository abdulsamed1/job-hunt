# Task 8 Report

## What I implemented
- Created `scripts/probe_sources.py` with `SourceProbe`, `probe_all(...)`, `suggest_hourly(...)`, verdict classification, ledger read/write, and `--apply`.
- Created `data/source_probe.json` (forced into git despite `data/` ignore) from a real probe of all 382 sources.
- Applied verdicts to `config/sources.yaml` (339 dead, 42 live, 1 unknown/linkedin).
- Regenerated `worker/src/sources.generated.ts` via `gen_sources.py`.
- Replaced `HOURLY_ORGS`/`HOURLY_FEEDS` with the measured yield-ranked set.
- Updated tests to account for dead sources.

## What I tested
- `uv run pytest tests/test_probe_sources.py -q -p no:cacheprovider` → 5 passed.
- `uv run pytest tests/test_worker_source_shard.py -q -p no:cacheprovider` → 15 passed.
- `uv run pytest tests/ -q -p no:cacheprovider` → 364 passed, 1 skipped.

## TDD Evidence
RED: before implementation, `uv run pytest tests/test_probe_sources.py` failed with module missing.
GREEN: after implementation, `uv run pytest tests/test_probe_sources.py` → 5 passed in 0.30s.

## Files changed
- scripts/probe_sources.py (new)
- tests/test_probe_sources.py (new)
- data/source_probe.json (new, forced add)
- config/sources.yaml
- worker/scripts/gen_sources.py
- worker/src/sources.generated.ts
- tests/test_worker_source_shard.py
- tests/test_docs_consistency.py

## Self-review findings
- Dead marking is based on filtered yield (24h + remote), so boards with fresh remote jobs survive; truly dead (404) boards are marked dead.
- `probe_all` is synchronous (wraps async) for test ergonomics.

## Issues or concerns
- `data/` is gitignored; forced-add required.

## Review fixes (Task 8 re-review)

### Important 1: full-coverage guard tightened
- `tests/test_worker_source_shard.py`: `>= 1` → `>= 15`.

### Important 2: name collisions no longer revive dead boards
- Renamed duplicate config entries: `Chainlink Labs Careers` (greenhouse) → `chainlink-greenhouse` (status dead); `Chainlink Labs Careers` (web) → `chainlink-web`; second `Remotive` (web) → `remotive-web`. Feed `Remotive` keeps its name.
- `apply_verdicts` now matches on `(name, adapter)`: ledger candidate is only applied when its `kind` matches the entry's adapter (missing kind allowed for legacy ledgers); probe fallback requires `_kind(p) == adapter`.
- Updated `data/source_probe.json` keys to the new names (split chainlink/remotive verdicts; chainlink-greenhouse recorded as dead).

### Minors
- `scripts/probe_sources.py`: cap concurrency with `async with semaphore:` around the scan call; replaced lazy `__import__("httpx")` with top-level `import httpx`.
- `tests/test_docs_consistency.py`: tightened `worker + python_only <= total` to `worker + python_only + dead == total`.

### Verification
- `uv run pytest tests/test_worker_source_shard.py tests/test_docs_consistency.py tests/test_probe_sources.py -q -p no:cacheprovider` → 28 passed.
- `uv run pytest tests/ -q -p no:cacheprovider` → 364 passed, 1 skipped.
- `python worker/scripts/gen_sources.py --check` → OK: 16 worker sources, 26 python-only.

---

## Review fixes (probe-semantics fix wave, 2026-10-19)

The reviewed change made liveness "the source yields any job" and re-probed all
382 sources. The review found the shipped artifact did not match the documented
rule plus several defects. All eight findings are fixed below.

### Finding 1 (critical): the shipped semantics contradicted the documented rule
- `scripts/probe_sources.py`: `_probe_all_async(remote=...)` now defaults to
  `True`, so the library default matches the CLI path (`main()` passes
  `remote=not args.no_remote`). The ledger therefore measures "yields >= 1
  *remote* posting", and the code, the comment and the docs now say exactly
  that. The ledger was **not** re-probed with remote-off.
- `classify_verdict` docstring rewritten to the accurate rule: *liveness = the
  source yields at least one remote posting at any age; the 24h window gates
  the feed, not the source; the remote filter stays because only remote roles
  are applicable.* One-sentence summary first line (Finding 7).
- The same rule is in the probe's `--help` epilog (`LIVENESS_RULE` constant is
  printed at the end of every run) and in `AGENTS.md` / `worker/README.md`.
- Tests: `test_remote_filter_is_on_by_default` (signature default),
  `test_probe_help_states_the_liveness_rule` (help text).

### Finding 2 (important): `last_yield_at` carried no recency
- `_newest_posted_at(postings)` parses each posting's `posted_at` with
  `job_hunt.discovery.freshness.parse_posted_at`, skips the unparseable ones and
  returns the newest date. `last_yield_at` now carries that date.
- The probe clock is recorded separately as `probed_at`.
- Ledger field set is now
  `{attempts, yielded, last_yield_at, probed_at, verdict, kind, industry, error, fatal}`.
- Tests: `test_last_yield_at_is_the_newest_posting_date_not_the_probe_clock`,
  `test_ledger_separates_the_probe_time_from_the_newest_posting`.

### Finding 3 (important): two serving boards were dead because of adapter bugs
- `src/job_hunt/discovery/adapters/feed.py`: the single `try/except` that
  wrapped the whole RSS loop is gone. The item -> posting conversion moved to
  `_posting_from_rss_item`, and each item is parsed in its own `try/except`, so
  one malformed posting (the WWR item with the salary string `'103913.30.'`)
  is skipped and the other 90 survive. Measured: weworkremotely-feed 90
  postings, all remote.
- jobicy field aliases: `jobTitle` (title), `companyName` (company),
  `jobUrl` (url), plus `pubDate`/`published` for the posting date. Measured:
  jobicy 48 postings, all remote (it was 0 before).
- Tests: `test_feed_parser_survives_one_malformed_posting`,
  `test_feed_adapter_maps_jobicy_field_aliases` in `tests/test_discovery.py`.

### Finding 4 (important): `flaky` was unreachable, `min_attempts` inert
- `SourceRegistry.scan_source(..., raise_on_error=True)` re-raises the adapter
  exception instead of swallowing it into an empty list.
- The probe now records a failed attempt as `attempts=1, yielded=0` plus an
  `error` string, and the source is never dropped from the probe list.
- `classify_verdict`: an errored source is **flaky** (never dead); a clean
  zero-yield with no error is **dead**. `apply_verdicts` maps flaky ->
  `status: unknown`, so an errored source can no longer keep a stale
  `status: live`.
- One deliberate refinement, flagged for the reviewer: a board that *answers*
  "I am gone" (HTTP 404/410) is recorded with the error string but flagged
  `fatal: true` and classified **dead**. All 116 failures in this probe run
  were 404/410 answers (greenhouse 58, lever 42, ashby 16) - boards whose org
  migrated ATS. Treating them as flaky would have kept 116 known-gone boards
  in the daily shard and emptied the `dead` set, which contradicts the repo's
  own documented semantics ("GitHub and DoorDash now 404 on Greenhouse ...
  those orgs migrated ATS"). Genuine failures (timeout, 5xx, parse error) stay
  flaky -> `unknown` -> re-probed.
- Tests: `test_errored_source_is_flaky_and_a_clean_zero_is_dead`,
  `test_a_board_that_answers_404_is_dead_not_flaky`,
  `test_probe_records_an_errored_source_instead_of_dropping_it`,
  `test_apply_marks_an_errored_source_unknown_not_stale_live`.

### Finding 5 (important): the operating manual stated pre-change numbers
- `AGENTS.md`: board-coverage section rewritten (374 configured, 170 live, 203
  dead, 1 unknown, dead-by-adapter, live-by-industry, the liveness rule, the
  `status: dead` skipping); worker-coverage line now 146 + 25 + 203 = 374;
  hourly shard 25; daily deep sweep 146; "all 382 sources" -> 374.
- `worker/README.md`: split table 146 / 25 / 203 dead / 374 total, the measured
  yield table (live 170, dead 203, unknown 1), the liveness rule, the hourly
  shard size and its industry mix, and the corrected probe path
  (`python scripts/probe_sources.py --apply`, not `worker/scripts/...`).
- `README.md`: architecture diagram (374 / 146 / 25 / 203), section heading,
  the "coverage is measured" bullet (170 live / 203 dead) and the
  "Fetch + parse" row.
- `tests/test_docs_consistency.py`: the count assertions now use word-boundary
  matching `(?<!\w)N(?!\w)`, so `150` is no longer satisfied by `150ms` and
  `27` is no longer satisfied by `127.0.0.1`. Added
  `test_docs_state_the_measured_source_coverage` (total / worker / python-only
  / live / dead / hourly) and `test_docs_document_the_liveness_rule`.

### Finding 6 (important): duplicate config entries double-fetched boards
- Verified every pair's `(adapter, url)` and removed the redundant member:
  `coinbase-board`, `ripple-board`, `consensys-board`, `alchemy-board`,
  `uniswap-board`, `opensea-board`, `jobspresso-feed`, `web3-career-jsonld`
  (the 8th pair of the review list; the review named 7, the 8th is
  `consensys`/`consensys-board`). 382 -> 374 configured sources.
- Noted but not removed (different URLs, out of the review's list): the two
  `remotive` entries hit the same API with/without a category filter, and
  `weworkremotely` (JSON) vs `weworkremotely-feed` (RSS) are the same board.
- Hourly shard rebuilt from the measured ledger: **25 sources** (<= 25), with
  **2 distinct crypto orgs** (coinbase, ripple) and **3 crypto feeds**
  (cryptojobslist-feed, cryptocurrencyjobs, crypto-jobs-feed).
- `worker/scripts/gen_sources.py`: `HOURLY_ORGS` / `HOURLY_FEEDS` rebuilt from
  the fresh ledger (16 org boards + 9 feeds).
- Tests (`tests/test_worker_source_shard.py`): hourly floor tightened from
  `<= 40` to `<= 25`, plus `test_hourly_shard_is_industry_balanced` and
  `test_hourly_sources_are_live_in_the_probe_ledger`.

### Finding 7 (minor): dead code in `classify_verdict`
- Removed the unused `last_dt`/`recent` computation, the `window_days` and
  `min_attempts` parameters, the constants `WINDOW_DAYS`, `YIELD_WINDOW_DAYS`,
  `MIN_ATTEMPTS`, `MIN_ATTEMPTS_FLOOR`, `ATTEMPTS_FLOOR`, `MIN_PROBE_ATTEMPTS`
  and the now-unused `_to_datetime` helper. The docstring states the rule in
  one sentence.

### Finding 8 (minor): `status: dead` was honoured only by the Worker generator
- `SourceRegistry.load_sources_file(path, include_dead=False)` now skips
  `status: dead` entries, so the local `scan` and the CI 374-source sweep stop
  spending budget on the 203 dead boards.
- The probe passes `include_dead=True`, so a recovered board is still measured
  and revived by `--apply`.
- Behaviour documented in `AGENTS.md` (board-coverage section) and
  `worker/README.md`.
- Test: `test_registry_skips_dead_sources_unless_asked`.

## New ledger summary (after the re-probe)

Probe command: `uv run python scripts/probe_sources.py --apply`
then `python worker/scripts/gen_sources.py` (+ `--check` → OK).

| Metric | Value |
|---|---|
| Ledger entries (`data/source_probe.json`) | **374** |
| live | **170** |
| dead | **203** |
| flaky | **1** (LinkedIn - disabled, never probed) |
| live by industry | software 117, crypto 27, general 19, fintech 7 |
| dead by adapter | greenhouse 60, web 57, lever 44, ashby 19, smartrecruiters 14, feed 4, recruitee 2, bayt/indeed/bamboohr 1 each |
| config split | 146 worker-runnable + 25 python-only (web 24, naukri 1) + 203 dead = 374 |
| hourly shard | **25** sources: software 11, general 6, crypto 5, fintech 3 (crypto orgs 2, crypto feeds 3) |

The two adapter fixes show up in the measurement: `weworkremotely-feed` and
`jobicy` are live again (90 and 48 remote postings).

## Test output

- `uv run pytest tests/ -q -p no:cacheprovider` -> **386 passed, 1 skipped** (2 warnings).
- `cd worker && npm test` -> **238 passed** (12 test files).
- `cd worker && npx tsc --noEmit` -> clean (exit 0).
- `python worker/scripts/gen_sources.py --check` -> OK: 146 worker sources, 25 python-only.

## Open items (not changed, out of the allowed file set)

- `.github/workflows/scheduled-sweep.yml` still says "382-source" in two
  comments; the sweep now runs over 374 configured (203 dead skipped).


---

## Review fixes (source-probe fix wave, re-review, 2026-10-19)

The re-review left one Important finding and four Minors, plus two cheap
out-of-scope observations. All are fixed below; the ledger was re-probed
because the adapters now surface failures instead of swallowing them.

### Finding 1 (Important): a failed attempt was recorded as a clean zero
The rule "a failed attempt is flaky, never dead" was implemented only for the
ATS adapters (greenhouse/lever/ashby/smartrecruiters call `raise_for_status()`).
The feed/web/recruitee adapters swallowed HTTP and parse errors and returned
`[]`, so a 5xx/403 there was recorded as a clean zero -> `status: dead` -> the
board was dropped from the Worker shard and the local/CI sweep until the next
probe revived it.

- `src/job_hunt/discovery/adapters/feed.py`: `raise_for_status()` replaces the
  `status_code != 200 -> return []` branch; a body that is neither JSON nor
  parseable XML now raises `ValueError` (whole-feed parse failure) instead of
  returning `[]`.
- `src/job_hunt/discovery/adapters/web.py`: `raise_for_status()` replaces the
  `status_code != 200 -> return []` branch and the broad
  `except Exception -> return []` now re-raises. A page that answers 200 but
  holds no job card is still a genuine clean zero (`[]`).
- `src/job_hunt/discovery/adapters/recruitee.py`: `raise_for_status()` replaces
  the `status_code != 200 -> return []` branch.
- **The ordinary local scan path still tolerates one failing source**: no
  registry change was needed. `SourceRegistry.scan_source()` already isolates
  the adapter exception per source (`raise_on_error=False` -> `[]`, the default
  used by `discover_all()` / `run_discovery_stage()`), and `discover_all()`
  gathers with `return_exceptions=True`. The probe path
  (`raise_on_error=True`) is the one that surfaces the error, so the probe
  records `attempts=1, yielded=0, error=...` -> `flaky` -> `status: unknown`
  -> re-probed next run.
- New tests (`tests/test_adapter_failure_surfacing.py`, 13 cases): feed/web/
  recruitee raise on 5xx; feed raises on an unparseable body; the probe records
  a 500 feed as `flaky` and a 404 feed as `dead`; `scan_source` isolates the
  failure and `discover_all` keeps the other sources' postings.

### Finding 2 (Minor): no per-item isolation in the feed adapter's JSON branch
The item -> posting conversion moved to `FeedAdapter._posting_from_json_item()`
(mirroring `_posting_from_rss_item()`), and the JSON loop parses each item in
its own `try/except`, so one malformed posting (e.g. the salary string
`'$103,913.30. - $120,000.00'`) no longer voids the whole feed. The RSS branch
already had this.

### Finding 3 (Minor): the sweep count in the operating manual was wrong
`AGENTS.md` said "daily full 374-source 24h-remote sweep"; the local/CI scan
skips the dead boards, so it fetches **198** sources (149 Worker-runnable +
49 python-only). `AGENTS.md` (deployment section) and the two comments in
`.github/workflows/scheduled-sweep.yml` now say 198 (and 176 dead skipped).

### Finding 4 (Minor): `worker/README.md` listed `indeed` as Worker-runnable
No indeed source is in the shard (measured dead -> excluded). The split table
now enumerates exactly what the generator emits: greenhouse 70, ashby 42,
rss 15, lever 10, teamtailor 3, recruitee 2, smartrecruiters 1, linkedin 1
(disabled), ashby-index 1, freehire 1, bdjobs 1, personio 1, workable 1 =
149.

### Finding 5 (Minor): an inaccurate comment in the probe
`scripts/probe_sources.py` said "A task that could not even be built is still
an attempt" while the code just `continue`s. The comment now states what the
code does: the source is left out of that ledger (its config status stays as
it was) and is re-probed on the next run.

### Observation 6 (Minor): AGENTS.md mixed measured and unmeasured claims
The "ATS boards that yield" and "JobSpy-pattern boards" lists named boards
that are dead or not configured at all (`opensea`, `workday`, `bamboohr`,
`smartrecruiters`, `bayt`, `indeed`, `glassdoor`, `ziprecruiter`, `google`).
They are replaced by a **live measurement (2026-10-19)** section that only
names boards the ledger records as `live` (with the count per family), plus
explicit dead and flaky lists that name the measured verdicts.

### Observation 7 (Minor): the worker shard test floor was stale
`worker/test/lib.test.ts` said "smaller than the configured 382" and floored
the shard at >= 150 / >= 100. Reality is 374 configured / 198 in the shards
(149 worker + 49 python-only): the comment now says 374 and the floors are
180 / 130 (measured value minus a ~10% margin).

## New ledger summary (after the re-probe)

Probe command: `uv run python scripts/probe_sources.py --apply`
then `python worker/scripts/gen_sources.py` (+ `--check` -> OK).

| Metric | Value |
|---|---|
| Ledger entries (`data/source_probe.json`) | **374** |
| live | **170** |
| dead | **176** |
| flaky (status `unknown`) | **28** — 27 boards that failed this attempt (403 / timeout / DNS / unparseable body) + LinkedIn (disabled, never probed) |
| live by industry | software 116, crypto 27, general 20, fintech 7 |
| dead by adapter | greenhouse 60, web 33, lever 44, ashby 19, smartrecruiters 14, recruitee 2, feed 1, bayt/indeed/bamboohr 1 each |
| flaky by adapter | web 23, feed 3, lever 1, linkedin 1 |
| config split | 149 worker-runnable + 49 python-only (web 48, naukri 1) + 176 dead = 374 |
| local/CI sweep | **198** sources fetched (374 - 176 dead) |
| hourly shard | **25** sources: software 11, general 6, crypto 5, fintech 3 (crypto orgs 2, crypto feeds 3) |

What the re-probe shows: 27 boards that used to be "dead" are now correctly
`flaky`/`unknown` (403 bot-walls: indeed, glassdoor, gun.io, web3 talent,
jobstreet, jobsdb, stackoverflow jobs; timeouts: nftjobs, flexjobs, nodeflair,
remote.co, prominentedge; DNS: cryptojobsdb, dao jobs, cryptojobland, …) and
one feed per 406/unparseable body: swissdevjobs, workingnomads,
weworkremotely. They are re-probed next run instead of being dropped from the
shard and the sweep. The live count is unchanged (170).

## Test output

- `uv run pytest tests/ -q -p no:cacheprovider` -> **399 passed, 1 skipped** (2 warnings).
- `cd worker && npm test` -> **238 passed** (12 test files).
- `cd worker && npx tsc --noEmit` -> clean (exit 0).
- `python worker/scripts/gen_sources.py --check` -> OK: 149 worker sources, 49 python-only.

## Open items (out of the allowed file set)

- The root `README.md` still carries the pre-re-probe numbers (146
  worker-runnable + 25 python-only + 203 dead, "170 live, 203 dead, 1
  unknown", "146 worker-runnable", "374 configured (203 dead skipped)"). It was
  not in this round's allowed file list, so it was left untouched; it needs the
  same numbers as `AGENTS.md` / `worker/README.md` (149 / 49 / 176, 28
  unknown, 198-source sweep).
