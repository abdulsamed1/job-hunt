# Disability-targeted job filtering and Telegram event sync

## Goal

Prevent automatic applications to postings intended specifically for people
with disabilities, including the Bayt Ignite example, and make the local
Python pipeline and Cloudflare Worker report their meaningful lifecycle events
to Telegram continuously.

## Scope

- Add one deterministic disability-targeting classifier in each runtime.
- Run the classifier before LLM scoring and before any application path.
- Keep ordinary postings that mention accessibility, accommodations, or equal
  opportunity without targeting disabled applicants.
- Send bounded, structured, source-tagged Telegram events from both runtimes.
- Preserve pipeline behavior when Telegram is unavailable or unconfigured.

Out of scope: inbound Telegram commands, raw log streaming, a new queue or
service, and changes to LinkedIn's disabled state.

## Architecture

The Python evaluator and Worker evaluator each use the same ordered phrase
rules. A match in the title or substantive description marks the posting as
pre-filtered, records a short reason containing the matched phrase, and keeps
it out of eligibility and application queues. Rules are deliberately
targeting-specific, such as `people with disabilities`, `persons with
disabilities`, `disability inclusion program`, and equivalent wording.
Generic EEO/accessibility language is not a match.

Telegram messages use a compact contract:

```text
[local|worker] EVENT: title @ company
reason/detail
url
```

Events are emitted for discovery/seeking, pre-filter/rejection, evaluation,
tailoring, application start, submission, failure, retry, and recovery.
Deduplication keys are runtime + job identity + event type. Messages are
truncated to Telegram's safe limit and never include tokens, cookies,
passwords, full resumes, or raw exception payloads.

## Data flow

1. Discovery normalizes and stores a posting.
2. The disability gate runs before scoring; a match is persisted as a
   pre-filtered/rejected outcome and notified.
3. Eligible jobs emit a seeking/evaluation event.
4. Tailoring and application state transitions emit their corresponding event.
5. Submission, failure, retry, and recovery emit terminal or operational
   events.
6. Telegram send failures are logged and do not change job state or cause
   queue retry storms.

The Python side uses its existing standard-library logging setup plus a
small Telegram handler/event helper. The Worker keeps its existing fetch
helper and once-only database bookkeeping, expanding it to event-level keys
instead of one notification flag per job.

## Validation

- Python tests reject the Bayt-style title and description wording.
- Python tests allow generic accessibility and equal-opportunity wording.
- Worker tests cover the same classifier behavior.
- Formatter tests verify source tags, event names, URL/detail truncation, and
  secret-safe output.
- Deduplication tests verify retries do not duplicate an event.
- Failure tests verify Telegram HTTP/network errors never stop pipeline work.
- Run the focused Python and Worker suites, then the full repository suite.

### Review Findings

Code review (BMAD bmad-code-review, 2026-10-16, branch sdd/slice9-disability-telegram @ a715355):
0 decision-needed (4 resolved 2026-10-16), 22 patch, 0 defer, 2 dismissed as noise.

- [x] [Review][Patch] Tighten the disability gate: FAIL on a title mention, a program/hiring noun (incl. plurals) within +/-80 chars, or disability-program phrasing; suppress only encouragement sentences without program/hiring nouns; keep generic EEO passing; regression tests both runtimes [src/job_hunt/evaluation/engine.py:281-301, worker/src/lib/evaluate.ts:64-87]
- [x] [Review][Patch] Rename the Worker RETRY event to STAGE_FAILED and remove RECOVERY from WorkerEvent (ack-and-alert stays) [worker/src/index.ts:363-382, worker/src/lib/notify.ts:1-2,24]
- [x] [Review][Patch] Emit APPLICATION_ATTEMPT at attempt start so the terminal event carries the dry-run outcome (both runtimes) [src/job_hunt/orchestrator.py:444,459-476, worker/src/index.ts:347,358-360]
- [x] [Review][Patch] Wire TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID from GitHub Secrets in scheduled-sweep.yml (web UI stays silent) [.github/workflows/scheduled-sweep.yml]
- [x] [Review][Patch] Add runtime CREATE TABLE IF NOT EXISTS plus a verify_deploy check for notification_events [worker/schema.sql:92, worker/src/state.ts:95, scripts/verify_deploy.py]
- [x] [Review][Patch] Make README failed-send semantics per-runtime accurate (local retries failed sends on a later emission; worker claims pre-send) [README.md:249-252, src/job_hunt/notifications.py:99-103]
- [x] [Review][Patch] Skip the ledger claim and legacy jobs.notified write when Telegram credentials are unset [worker/src/lib/notify.ts:112-135]
- [x] [Review][Patch] Fix worker per-invocation budget accounting: drop the reader-less notified UPDATE (or terminal-only), update the <=50 docstring [worker/src/index.ts:4-8, worker/src/lib/notify.ts:130-135]
- [x] [Review][Patch] Remove dead notifyJobOnce/eventKey or restrict the legacy notified flag to terminal events [worker/src/lib/notify.ts:66-68,146-166]
- [x] [Review][Patch] Emit lifecycle events (and run the disability gate) on tailor --job-id and apply --job-id [src/job_hunt/cli.py:210-218,224-258]
- [x] [Review][Patch] Re-run the disability gate at application time in both runtimes [src/job_hunt/orchestrator.py:380-492, worker/src/index.ts:297-361]
- [x] [Review][Patch] Delete the no-op EEO escape branch and correct related comments/test names [src/job_hunt/evaluation/engine.py:295-301, worker/src/lib/evaluate.ts:81-86]
- [x] [Review][Patch] Split sentences on ". " (whitespace or end) instead of a bare "." [src/job_hunt/evaluation/engine.py:166-180, worker/src/lib/evaluate.ts:47-62]
- [x] [Review][Patch] Python dedup: key on URL identity; document the process-lifetime FIFO 2000 behavior [src/job_hunt/notifications.py:22,90,105-110]
- [x] [Review][Patch] Offload the blocking Telegram send from async stages [src/job_hunt/notifications.py:42-52]
- [x] [Review][Patch] Docs drift: --no-notify missing from CLI table, stale test counts, worker/README "match alerts" and ledger gotcha, AGENTS.md notifications [README.md:201-212,273-274, worker/README.md:127]
- [x] [Review][Patch] Remove the unused RECOVERY member from WorkerEvent (or implement worker recovery) [worker/src/lib/notify.ts:1-2,24]
- [x] [Review][Patch] Avoid the REJECTED-then-PRE_FILTERED_OUT double audit row for gated evaluations [worker/src/index.ts:268-272, worker/src/state.ts:70-79]
- [x] [Review][Patch] Add shared cross-runtime gate parity fixtures [tests/test_evaluation.py, worker/test/lib.test.ts]
- [x] [Review][Patch] Strengthen leak tests: real notifier with stubbed transport; include company in the leak-field loop [tests/test_orchestrator.py:500-544]
- [x] [Review][Patch] Per-field caps in format_event so the URL cannot be truncated away [src/job_hunt/notifications.py:34-39]
- [x] [Review][Patch] Truncate gate reason quotes around the matched phrase ([:200] can omit it) [src/job_hunt/evaluation/engine.py:291, worker/src/lib/evaluate.ts:78]
