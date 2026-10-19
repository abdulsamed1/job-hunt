# Final whole-branch review — fix wave

Branch: `sdd/feed-quality` · Worktree: `1e16ed/feed-quality` · Date: 2026-10-19

Every finding of the final whole-branch review is fixed below, with the files
touched, the covering tests and the commands that prove it. Two places where the
review's wording and the branch's own fixtures disagreed are called out
explicitly in "Deviations".

---

## 1. The IC allow-list short-circuited the reject families

**Changed**

- `worker/src/lib/roles.ts` — `classifyRole` now evaluates the reject families
  **first** and nothing overrides them. The IC allow-list (seniority marker +
  engineering noun, or "lead engineer") only stands in for the software
  allow-list, so `Senior Sales Engineer`, `Staff Sales Engineer`,
  `Senior Marketing Engineer` and `Senior Customer Success Engineer` are
  `reject` (`revenue:*`) instead of `software`. A people-management title still
  wins (`Senior Engineering Manager` → `people-management:manager`).
  `hasFamilyMatch` (only used by the old short-circuit) is gone.
- `src/job_hunt/evaluation/engine.py` — same order in `pre_filter`: the reject
  families run first, the CRM check second, and the IC allow-list only replaces
  the positive SWE check. The negative patterns moved into a shared
  `ROLE_REJECT_RULES` table (`family`, `slug`, canonical word, pattern) so the
  reason names the rule the same way the Worker does
  (`Title matches negative filter: revenue:sales`).

**Covering tests**

- `worker/test/roles.test.ts` — `Senior Sales Engineer` → `revenue:sales`,
  `Staff Sales Engineer` → `revenue:sales`, `Senior Marketing Engineer` →
  `revenue:marketing`, `Senior Customer Success Engineer` →
  `revenue:customer success` added to the reject fixtures; new test
  "lets a revenue or non-engineering word beat the IC allow".
- `tests/test_evaluation.py` — the four revenue titles added to
  `test_pre_filter_rejects_non_ic_roles`; new test
  `test_pre_filter_names_the_reject_rule_the_worker_names`.
- The fixture `Staff Engineer, Sales Tools` (expected `software`) was narrowed
  to `Staff Engineer, Payments Tools` — it still tests allow-first ordering,
  without contradicting the new rule. The test that carried it was renamed
  (`test_pre_filter_keeps_an_ic_title_that_matches_no_reject_family`) and the
  Worker twin renamed to "keeps an IC title that matches no reject family, and
  rejects management titles", because "allows the IC title before checking
  negatives" is no longer what the code does.

**Cross-runtime proof** (throwaway script, not committed): 69 titles
classified by both runtimes → **0 divergences** after the fix (1 before:
`Full-Stack Engineer`).

---

## 2. The role gate never demoted a pre-existing row

**Changed**

- `worker/src/stages/discover.ts` — when the posting's own row exists and is
  gateable (`GATEABLE_STATES`), the role gate now calls
  `setJobState(db, hash, "PRE_FILTERED_OUT", reason)`, mirroring the duplicate
  gate. A manager/revenue posting stored before the gate existed therefore
  leaves the feed instead of sitting in `/recent`'s actionable list.
  The same demotion was added to the **dateless** branch (same defect, same
  file): a posting that lost its date between two sweeps is moved out too.
  There is no D1 backfill in this branch, so the fix belongs here.

**Covering tests**

- `worker/test/stages.test.ts` — the test "explains a posting stored before the
  gate even when its jobs upsert is a no-op" now seeds the pre-gate row
  (`seedJobs`) and asserts the demotion (`UPDATE jobs SET state` →
  `PRE_FILTERED_OUT`, row state `PRE_FILTERED_OUT`) instead of asserting that
  no state update happened.
- New tests: "demotes a pre-existing revenue posting the role gate matches" and
  "demotes a posting that lost its date between two sweeps".

---

## 3. `README.md` contradicted the measured coverage

**Changed**

- `README.md` — line 11: `146 Worker-runnable + 25 Python-only + 203 dead` →
  `149 Worker-runnable + 49 Python-only + 176 dead`; line 74: `170 live`,
  `203 dead`, `1 unknown` → `170 live`, `176 dead`, `28 unknown` (+ the 198
  boards the local/CI sweep fetches); line 189: `146 worker-runnable` /
  `203 dead skipped` → `149 worker-runnable` / `176 dead skipped → 198 fetched`.
- `tests/test_docs_consistency.py` — `test_docs_state_the_measured_source_coverage`
  now asserts **per document** (`README.md`, `AGENTS.md`, `worker/README.md`):
  each one must state the configured, worker-runnable, python-only, live, dead
  and unknown counts. The old guard concatenated all four documents and only
  asked whether a number appeared *somewhere*, so a correct `AGENTS.md` masked
  a wrong `README.md`.
- `tests/test_docs_consistency.py::test_no_stale_counts` — the stale counts
  `146`, `203` and `25 Python-only` are now rejected. `25` alone stays legal
  (the hourly scout shard really is 25 sources), so only its stale
  python-only shape is forbidden; the reason is in a comment.

**Proof the guard bites**: putting `146` back into `README.md` fails
`test_no_stale_counts` (verified by a temporary edit, then reverted).

---

## 4. The two runtimes' role vocabulary diverged

**Changed**

- `worker/src/lib/roles.ts` — the `intern` rule now matches `intern` **and**
  `internship` (same slug `non-engineering:intern`), so "Software Engineering
  Internship" is rejected; the `full stack` rule now matches `full stack`,
  `fullstack` **and** (via punctuation flattening) `full-stack`, so "FullStack
  Engineer" is `software`.
- `src/job_hunt/evaluation/engine.py` — the positive patterns accept the
  hyphenated spelling too (`\bfull[\s-]*stack\b`), so the two runtimes agree on
  "Full-Stack Engineer" as well (the Worker already did).

**Covering tests** (both suites, so they cannot drift again)

- `worker/test/roles.test.ts`: `Software Engineering Internship` in the reject
  fixtures, `FullStack Engineer` and `Full-Stack Engineer` in the accept list.
- `tests/test_evaluation.py`: `test_pre_filter_rejects_internship_titles`
  ("Software Engineering Internship", "Backend Engineering Internship") and
  `test_pre_filter_accepts_fullstack_titles` ("FullStack Engineer",
  "Full-Stack Engineer", "Full Stack Engineer").

---

## 5. `/coverage` loaded the whole `jobs` table

**Changed** (`worker/src/index.ts`)

- The jobs read is now windowed and bounded:
  `SELECT title, company, source, posted_at, state FROM jobs
   WHERE discovered_at >= datetime('now', '-1 day')
   ORDER BY discovered_at DESC LIMIT ?` with `COVERAGE_ROW_LIMIT = 2000`.
- The aggregate fallback is **deleted**: it queried columns that do not exist in
  real D1 and could silently report "all companies ever". The endpoint now
  fails loudly instead, and the `try/catch` that swallowed the jobs read is gone
  too (the counters read keeps its catch: that table may legitimately be
  missing on a pre-migration deploy).

**Covering tests** (`worker/test/health.test.ts`)

- The existing `/coverage` test's D1 double now answers the bound statement.
- New test "reads the jobs table through a window and a limit, never in full":
  asserts the query carries `discovered_at >=` and `LIMIT`, and that neither the
  `dated_fresh` aggregate nor `COUNT(DISTINCT company)` is ever asked for.

---

## 6. Seven adapters still swallowed failures

**Changed** (all seven now raise on a non-success HTTP status; a parse failure
propagates)

- `src/job_hunt/discovery/adapters/teamtailor.py` — `raise_for_status()`.
- `src/job_hunt/discovery/adapters/workable.py` — `raise_for_status()`; a
  non-JSON body raises `ValueError`.
- `src/job_hunt/discovery/adapters/personio.py` — `raise_for_status()`.
- `src/job_hunt/discovery/adapters/bamboohr.py` — `raise_for_status()`; a
  non-JSON body raises `ValueError`.
- `src/job_hunt/discovery/adapters/ashby_index.py` — a transport failure raises
  `ValueError`, a non-success status raises `HTTPStatusError`. Per-org failures
  inside the fan-out stay isolated (one board must not void the index).
- `src/job_hunt/discovery/adapters/indeed.py` — the retry loop raises
  `HTTPStatusError` on a non-success status and `ValueError` on an unexpected
  payload instead of returning "no results".
- `src/job_hunt/discovery/adapters/bayt.py` — the retry loop raises on a
  non-success status; a 404/410 raises an `HTTPStatusError` that carries the
  response, so the probe can still tell "gone" (dead) from "failed" (flaky).

Both multi-page boards (Indeed, Bayt) keep the postings they already collected
when a later page fails — liveness is "yields at least one remote posting", so
a partial answer is still an answer — and re-raise when nothing was collected.
Bayt keeps its "one location 404 does not stop the other locations" behaviour:
the 404 is remembered and re-raised only if no location yielded anything.

**Covering tests** (`tests/test_adapter_failure_surfacing.py`)

- New tests per adapter: teamtailor (500 + unparseable feed), workable (503 +
  non-JSON), personio (500), bamboohr (403), ashby-index (500 + transport
  failure), indeed (500 + unexpected payload), bayt (failure + 404 carrying its
  status).
- New test "discover_all_keeps_the_other_sources_when_a_tenant_board_fails":
  the ordinary local scan still tolerates one failing source (the registry
  isolates it per source) — the scan does not abort.

---

## 7. The shard-size guard floor

**Changed** — `tests/test_worker_source_shard.py`: `assert len(worker) >= 15` →
`>= 100` (the shard is 149 today), and the docstring now says what the floor
means instead of claiming "full coverage". The Worker-side equivalent
(`worker/test/lib.test.ts`) was already meaningful and is untouched (except
the comment fix in #13).

---

## Minors folded in

8. **`findKnownJobs` keys are namespaced** (`worker/src/state.ts`,
   `worker/src/stages/discover.ts`): `h:` for a canonical hash, `f:` for a role
   fingerprint (`hashKey` / `fingerprintKey`), so a cross-kind collision cannot
   resolve one posting's identity to another row's. Tests:
   `worker/test/state.test.ts` (namespaced keys + a new "keeps the two identifier
   kinds in separate key spaces" test).
9. **Python's `COMPANY_SUFFIXES` and `ROLE_TOKEN_EXPANSIONS` ported to the
   Worker** (`worker/src/lib/hash.ts`): `normalizeCompany` / `normalizeRole`,
   used by `roleFingerprint`, so "Acme, Inc." ≡ "Acme" and "Sr. Backend
   Engineer" ≡ "Senior Backend Engineer" in the runtime that actually applies.
   Verified against Python's `dedup.py` on 15 cases → 0 divergences. Tests:
   `worker/test/lib.test.ts` ("collapses the legal company forms Python
   collapses", "collapses the role abbreviations Python collapses").
10. **`scripts/backfill_feed_quality.py` no longer carries a third copy of the
    role rules**: it imports `classify_role_reject` from
    `src/job_hunt/evaluation/engine.py` (the shared `ROLE_REJECT_RULES` table).
11. **`worker/src/index.ts`**: the evaluate-stage SELECT now reads `posted_at`,
    so `ELIGIBLE`/`FILTERED` alerts can render the posting's age.
    **`worker/src/lib/notify.ts`**: `formatJobAlert` renders the age segment
    only when the row has a date (an unreadable date still says "date
    unknown"; a row with no date says nothing about one). Tests:
    `worker/test/notify.test.ts`.
12. **The dateless bucket is capped** at the same `INTAKE_CAP` (30) as the
    fresh path, and the held-back postings are counted in `intake_capped`
    (`worker/src/stages/discover.ts`). Test: "caps the dateless bucket too, and
    counts what was held back".
13. **Doc one-liners**: `AGENTS.md` no longer lists `ethgigs` under the dead
    boards (no such entry exists — the closest is "Ethereum Jobs", flaky) and
    no longer claims bayt/indeed "answer 404" (the ledger records a clean zero
    with no cause; the adapters now raise, so the next probe records the real
    status); `worker/README.md:126` says "the 24h + remote filters";
    `worker/test/lib.test.ts` no longer calls the non-dead count (198) the
    "measured live count" (live is 170).
14. **`worker/src/stages/discover.ts` ends with a newline.**

---

## Deviations / judgement calls

- **Finding 1, wording.** The review says "let the IC allow override *only* a
  people-management match". Taken literally, "Senior Engineering Manager"
  would become `software` — which contradicts the branch's own fixture
  (`["Senior Engineering Manager", "people-management:manager"]`) and the
  point of the people-management family. The implemented rule is therefore
  "the IC allow overrides **nothing**": the reject families are evaluated
  first and always win, and the IC allow only replaces the *positive* software
  check. This satisfies the finding's operative requirement (revenue, CRM and
  non-engineering always win) and keeps every existing fixture true.
- **Finding 3, stale count "25".** `25` is a live number in the docs (the
  hourly scout shard is 25 sources), so the stale-count guard forbids the bare
  numbers `146` and `203` and the phrase `25 Python-only` — with a comment
  saying why.
- **Finding 6, Indeed/Bayt partial answers.** A board that answers with
  postings before failing is still live, so the postings are kept; a board that
  fails without answering re-raises (flaky) instead of reporting a clean zero
  (dead).
- **Finding 9, fingerprint change.** The Worker's `role_fingerprint` values
  change with this commit (the normalization changed). Rows stored before the
  deploy keep their old fingerprint until they are re-discovered and rewritten;
  the Dedupe gate therefore needs one sweep to re-key the store. This is
  inherent to the requested change (the Worker must collapse the same surface
  forms Python does).

---

## Commands and output

```
$ cd worker && npm test
 Test Files  12 passed (12)
      Tests  254 passed (254)

$ cd worker && npx tsc --noEmit
(clean, no output)

$ uv run pytest tests/ -q -p no:cacheprovider
420 passed, 1 skipped, 2 warnings in 112.56s (0:01:52)
```

## Files changed

- `AGENTS.md`, `README.md`, `worker/README.md`
- `scripts/backfill_feed_quality.py`
- `src/job_hunt/evaluation/engine.py`
- `src/job_hunt/discovery/adapters/{ashby_index,bamboohr,bayt,indeed,personio,teamtailor,workable}.py`
- `worker/src/index.ts`, `worker/src/lib/{hash,notify,roles}.ts`,
  `worker/src/stages/discover.ts`, `worker/src/state.ts`
- `worker/test/{health,lib,notify,roles,stages,state}.test.ts`
- `tests/test_adapter_failure_surfacing.py`, `tests/test_discovery.py`,
  `tests/test_docs_consistency.py`, `tests/test_evaluation.py`,
  `tests/test_worker_source_shard.py`
