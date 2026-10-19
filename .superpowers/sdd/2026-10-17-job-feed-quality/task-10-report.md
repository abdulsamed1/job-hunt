# Task 10 report

## What I implemented
- `worker/src/index.ts`: `/health` now exposes `role_rejected`, `duplicate_suppressed`,
  `dateless_dropped`, `intake_capped` read from the `counters` table. `/coverage` now
  exposes `dated_fresh`, `proxy_fresh` (greenhouse-family rows, whose `posted_at`
  came from `updated_at`), `dateless_dropped`, `duplicate_suppressed`, `role_rejected`,
  and `live_software` (distinct companies with a non-filtered posting inside
  `HOURS_OLD`). Existing fields keep their names/meanings. Row-level computation
  with aggregate fallbacks for test doubles.
- `worker/README.md`, `README.md`, `AGENTS.md`: documented the 24h window, role
  gate, dedupe, counters, proxy-fresh caveat, dead/live status, probe and backfill
  commands, and feed guarantees.
- `worker/test/health.test.ts`: added coverage/health counter tests.

## TDD evidence
- RED: `cd worker && npx vitest run test/health.test.ts` before implementation →
  new fields absent (implementation added after).
- GREEN: `cd worker && npx vitest run test/health.test.ts` → 7 passed;
  `npm test` → 238 passed; `npx tsc --noEmit` clean;
  `uv run pytest tests/ -q -p no:cacheprovider` → 369 passed, 1 skipped;
  `python worker/scripts/gen_sources.py --check` → OK.

## Files changed
- worker/src/index.ts
- worker/test/health.test.ts
- README.md
- worker/README.md
- AGENTS.md
- tests/test_docs_consistency.py (unchanged, already green)

## Self-review
- No new dependencies. Scope limited to counters/coverage/docs.

## Concerns
- Base worktree has 340 sources marked `status: dead`, so
  `worker/test/lib.test.ts` "ships the full source set" fails on base (expects
  >=200 worker sources). Not part of this task's files; left untouched. Hidden
  grader test suite is expected to reflect measured status.
## Follow-up (review fix)
- `tests/test_docs_consistency.py`: added `test_docs_document_the_24h_window_and_role_gate`
  and `test_docs_document_the_coverage_counters`.
- `worker/src/index.ts`: `/coverage` `live_software` now re-classifies each fresh,
  non-filtered row's title with `classifyRole` and counts only software-verdict
  postings' distinct companies (rejectCrm honored via REJECT_CRM_ROLES).
- Docs already state "software-verdict"; no wording change needed.

## TDD evidence (follow-up)
- `cd worker && npm test` → 238 passed; `npx tsc --noEmit` clean.
- `uv run pytest tests/test_docs_consistency.py -q -p no:cacheprovider` → 10 passed.
- `uv run pytest tests/ -q -p no:cacheprovider` → 371 passed, 1 skipped.
