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
