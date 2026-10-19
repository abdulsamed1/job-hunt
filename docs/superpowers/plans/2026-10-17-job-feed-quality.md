# Job Feed Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the bot's feed contain only software-engineering roles posted within 24 hours, one
application per requisition, drawn from a genuinely broad source set instead of a crypto-heavy few.

**Architecture:** Four enforcement points on the Worker (the 24/7 runtime) plus parity in Python. A
pure `classifyRole` module decides software / reject / unknown before a posting is ever stored or
notified; a role fingerprint (company + normalized title + normalized location) gives every posting a
second identity so the same requisition from several boards collapses to one; the recency window
becomes a single named 24h constant that also gates the apply path, and dateless postings are stored
but never scored; coverage stops being a hand-kept crypto allowlist and becomes a measured artifact
produced by a probe script. Counters make all of it observable on `/health` and `/coverage`.

**Tech Stack:** Cloudflare Workers (fetch/queue handler, D1, R2), TypeScript + vitest; Python 3
stdlib + pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-17-job-feed-quality-design.md` — read it before Task 1; the
plan implements it and the decisions in its "Decisions taken" section are binding.

## Global Constraints

- No new npm or Python dependencies. `pdf-lib` and the Workers runtime stay as they are.
- Worker free tier: 5 cron triggers, 50 subrequests/queries per invocation, queue batch caps. The role
  classifier is pure CPU (no I/O); the duplicate check is exactly one batched `IN (...)` query per
  source invocation. Per-posting D1 cost is bounded: a newly stored posting may cost one counter bump,
  and a posting that is re-discovered later costs no counter bump, no detail write and no notification.
- Zero paid APIs. Board probing uses the product's own adapters.
- Safety flags are unchanged: `LIVE_APPLY=false`, `LINKEDIN_ENABLED=false`, and LinkedIn stays
  filtered out of every shard until the owner explicitly re-enables it.
- Recency window is **24 hours** everywhere: `config/sources.yaml` per-source `hours_old` values above
  24 are clamped to 24, and the Worker's window is the single constant `HOURS_OLD = 24`.
  `hours_old: 0` (window disabled) stays available for a deliberate manual run only, never for a
  scheduled one.
- Dateless postings are stored for the record, counted as `dateless_dropped`, and can never reach
  ELIGIBLE, be notified as SEEKING, or be applied to.
- Greenhouse `updated_at` within 24h counts as freshness, reported separately as `proxy_fresh` so it is
  never confused with a real posting date.
- `worker/src/sources.generated.ts` is generated and never hand-edited: change `config/sources.yaml`,
  then run `python worker/scripts/gen_sources.py`; `python worker/scripts/gen_sources.py --check` must
  pass in CI (`tests/test_worker_source_shard.py` enforces it).
- Notification event names and the existing reply/reasoning prefixes stay stable; role and freshness
  rejections reuse `FILTERED`, duplicates reuse `DUPLICATE`, and neither adds a new state.
- Commits are conventional (`feat:`, `fix:`, `test:`, `docs:`, `chore:`).
- Suites green before every commit: Worker changes run `npm test` and `npx tsc --noEmit` in `worker/`;
  Python or config changes run `uv run pytest tests/ -q -p no:cacheprovider` at the repo root. A task
  touching neither does not re-run unrelated suites.

## Review Focus

The spec implies these input classes; no existing test exercises them. Each is pinned by the task
listed, and a reviewer should treat an unpinned one as a gap in that task.

1. A title matching both an IC allow and a reject ("Lead Software Engineer" vs "Engineering Team
   Lead") must resolve by allow-first, never by pattern order. → Task 1
2. One requisition published on two boards with different location strings ("Remote, US" vs "United
   States" vs empty) must produce one application and one SEEKING. → Task 3
3. A greenhouse posting whose `updated_at` is inside 24h but which was first published months ago is
   accepted as `proxy_fresh` and counted separately from `dated_fresh`. → Tasks 4 and 10
4. A dateless posting from any source is stored, counted, never scored, never notified. → Task 4
5. A source marked `status: dead` that starts yielding again must be revivable by the probe without a
   hand edit of generated code. → Tasks 7 and 8

---

### Task 1: Role classifier module

**Files:**
- Create: `worker/src/lib/roles.ts`
- Test: `worker/test/roles.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces: `classifyRole(title: string, opts?: { rejectCrm?: boolean }): { verdict: "software" | "reject" | "unknown"; rule: string }` — `rule` is a stable slug (`people-management:manager`, `revenue:sales`, `crm:salesforce`, `non-engineering:recruiter`, `software:ic`, `software:stack`, and one per technology word (`software:backend`, `software:devops`, …), `unknown:none`), never an empty string. Later tasks key on `verdict`, never on a software slug.
  Task 2 imports this exact name and shape.

- [ ] **Step 1: Write the failing tests**

Create `worker/test/roles.test.ts` with one table-driven test per rule family plus the ordering and
default cases. Real titles that leaked are the fixtures:

```ts
import { describe, expect, it } from "vitest";
import { classifyRole } from "../src/lib/roles.js";

// rejects (verdict "reject")
const REJECTS: Array<[string, string]> = [
  ["Senior Manager, Application Security", "people-management:manager"],
  ["Engineering Team Lead, Platform", "people-management:team lead"],
  ["Head of Engineering", "people-management:head of"],
  ["Director of Software Engineering - Distributed Systems", "people-management:director"],
  ["VP of Engineering", "people-management:vp"],
  ["Deputy Department Manager, PRE Division Product SRE", "people-management:manager"],
  ["Account Executive, EMEA", "revenue:account executive"],
  ["Sales Development Representative", "revenue:sales development"],
  ["Marketing Manager", "revenue:marketing"],
  ["Customer Success Manager", "revenue:customer success"],
  ["Technical Recruiter", "non-engineering:recruiter"],
  ["Product Manager", "non-engineering:product"],
  ["Software Engineering Intern", "non-engineering:intern"],
];

// accepts (verdict "software")
const ACCEPTS = [
  "Senior Software Engineer, Query",
  "Backend Engineer, Payments",
  "Full Stack Engineer",
  "Lead Software Engineer",          // IC allow must beat nothing; no reject word here
  "Staff Engineer, Platform",
  "Principal Engineer, Data",
  "Forward Deployed Engineer",
  "Site Reliability Engineer",
  "Android Engineer",
  "Firmware Engineer",
  "QA Automation Engineer",
  "Security Engineer, Product",
  "Node.js Engineer",
  "React Developer",
];

// CRM roles are rejected only while rejectCrm is on (default on)
const CRM = [
  "Senior Salesforce Developer",
  "Salesforce Consultant",
  "Salesforce Architect",
  "Salesforce Administrator",
];

describe("classifyRole", () => {
  it.each(REJECTS)("rejects %s", (title, rule) => {
    expect(classifyRole(title)).toEqual({ verdict: "reject", rule });
  });
  it.each(ACCEPTS)("accepts %s", (title) => {
    expect(classifyRole(title).verdict).toBe("software");
  });
  it.each(CRM)("rejects CRM role %s by default", (title) => {
    expect(classifyRole(title).verdict).toBe("reject");
    expect(classifyRole(title).rule.startsWith("crm:")).toBe(true);
  });
  it("allows CRM roles when rejectCrm is off", () => {
    expect(classifyRole("Senior Salesforce Developer", { rejectCrm: false }).verdict).not.toBe("reject");
  });
  it("prefers the IC allow over a reject word (allow-first ordering)", () => {
    expect(classifyRole("Lead Software Engineer").verdict).toBe("software");
    expect(classifyRole("Engineering Lead, Software").verdict).toBe("software");
    expect(classifyRole("Team Lead, Backend Engineering").verdict).toBe("reject");
  });
  it("returns unknown for titles with no signal", () => {
    expect(classifyRole("")).toEqual({ verdict: "unknown", rule: "unknown:none" });
    expect(classifyRole("Data Scientist").verdict).toBe("unknown");
    expect(classifyRole("Guru of spreadsheets").verdict).toBe("unknown");
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd worker && npx vitest run test/roles.test.ts`
Expected: FAIL — cannot resolve `../src/lib/roles.js`.

- [ ] **Step 3: Implement `classifyRole` in `worker/src/lib/roles.ts`**

Evaluation order is fixed: normalize the title (lowercase, collapse whitespace, strip punctuation for
matching), then (1) reject families — which always win, (2) the IC allow list, which replaces the
positive software check, (3) unknown. Never reorder these: an earlier draft checked the IC allow
first, which let a seniority marker rescue a sales or marketing title.

| Family | Matching words (title text, word-boundary matched) |
|---|---|
| IC allow (replaces the positive software check; never overrides a reject family) | `lead engineer`, `lead software engineer`; bare `staff`, `principal`, `senior`, `sr` count as IC only when the title also names `engineer` or `engineering` (otherwise `Senior Manager…`, `Senior Salesforce Developer` and `Senior Engineering Manager` would each be rescued by a seniority marker) |
| people-management reject | `manager`, `team lead`, `group lead`, `head of`, `director`, `vp`, `chief`, `supervisor` |
| revenue reject | `sales`, `sales development`, `business development`, `account executive`, `account manager`, `marketing`, `partner`, `customer success`, `customer support` |
| CRM reject (only when `rejectCrm !== false`) | `salesforce`, plus any of `consultant`, `architect`, `administrator`, `analyst`, `developer` in the same title — the plural of a companion is a companion (`Salesforce Developers` rejects like `Salesforce Developer`), and no new vocabulary is added, so `Software Engineer, Salesforce Integration` stays a software role |
| non-engineering reject | `recruiter`, `talent acquisition`, `human resources`, `accountant`, `finance`, `legal`, `nurse`, `medical`, `driver`, `content writer`, `graphic designer`, `intern`, `product manager` — the domain words (`accountant`, `finance`, `legal`, `nurse`, `medical`) fire only when the title names no engineering build role (`engineer`, `developer`, `devops`, `architect`, `programmer`, `sre`), so `Full Stack Engineer - Finance` is a software role and `Senior Accountant` is not |
| software allow | `software engineer`, `software developer`, `backend`, `frontend`, `front end`, `full stack`, `platform engineer`, `site reliability`, `sre`, `devops`, `cloud engineer`, `infrastructure`, `distributed systems`, `systems engineer`, `data engineer`, `ml engineer`, `machine learning`, `mobile`, `ios`, `android`, `qa`, `sdet`, `test engineer`, `security engineer`, `embedded`, `firmware`, `forward deployed engineer`, the bare word `software` (so `Engineering Lead, Software` matches), and any of `nodejs`, `node.js`, `typescript`, `react`, `reactjs`, `javascript`, `python`, `golang`, `rust`, `java` combined with `engineer` or `developer` |

`opts.rejectCrm` defaults to `true`. Return the most specific matched slug as `rule`: collect every
reject-family match and take the longest matched word, first-listed on a tie (so `Marketing Manager`
reports `revenue:marketing` and `Product Manager` reports `non-engineering:product`). Known side effect
of the bare `software` word: trainer-style titles classify as `software` and are capped below the
eligibility threshold in Task 2 — visible in the feed, never applied.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd worker && npx vitest run test/roles.test.ts`
Expected: PASS, all files green, output pristine.

- [ ] **Step 5: Commit**

```bash
git add worker/src/lib/roles.ts worker/test/roles.test.ts
git commit -m "feat(worker): role classifier for a software-only feed"
```

---

### Task 2: Enforce the role gate in discovery and evaluation

**Files:**
- Modify: `worker/src/index.ts` (discover loop where `upsertJob` + `EVALUATE_Q.send` + SEEKING happen, currently `for (const j of enriched)` after `enrichDescriptions(remote.slice(0, 40))`; `scoreJob` call site in the `evaluate` stage)
- Modify: `worker/src/lib/evaluate.ts` (`scoreJob`, currently returns `{ score, eligible, matched, missing, reasoning, gated }`)
- Modify: `worker/src/state.ts` (add `ensureCounters(db)` and `bumpCounter(db, key, n = 1)`; `upsertJob` gains an optional `state` so a rejected posting is stored in its rejected state)
- Test: `worker/test/stages.test.ts` (discover + evaluate paths on `memQueueDb`), `worker/test/state.test.ts` (counters table)

**Interfaces:**
- Consumes: `classifyRole` from Task 1.
- Produces: `ensureCounters(db: Db): Promise<void>` and `bumpCounter(db: Db, key: string, n?: number): Promise<void>` in `worker/src/state.ts`, backed by a `counters(key TEXT PRIMARY KEY, value INTEGER NOT NULL)` table created by a runtime migration mirroring `ensureSourceHealthColumns`. `scoreJob` keeps its return shape and adds the role reasoning to `reasoning` with `gated: true` for rejects. Tasks 3, 4 and 10 use `bumpCounter` with keys `role_rejected`, `duplicate_suppressed`, `dateless_dropped`.

- [ ] **Step 1: Write the failing tests**

Append to `worker/test/stages.test.ts`, driving the queue handler the way the existing discover tests
do:

```ts
it("stores a manager posting as PRE_FILTERED_OUT and never sends SEEKING or an evaluate message", async () => {
  // source returns one posting titled "Senior Manager, Application Security"
  // assert: job row state is PRE_FILTERED_OUT, its reasoning contains "people-management:manager",
  // counters.role_rejected is 1, no SEEKING notification row, no evaluate queue message
});

it("keeps SEEKING for a software posting and queues its evaluation", async () => {
  // source returns "Senior Software Engineer, Query"
  // assert: state DISCOVERED, SEEKING recorded, one evaluate queue message
});

it("gates evaluation of a manager posting with score 0", async () => {
  // run the evaluate stage for a stored manager row
  // assert: score 0, gated true, reasoning contains the matched rule
});

it("caps an unknown-title posting below the eligibility threshold", async () => {
  // source returns "Data Scientist" with strong skill text
  // assert: score <= 69, eligible false
});
```

And to `worker/test/state.test.ts`: `ensureCounters` is idempotent across two calls, and
`bumpCounter` accumulates (`role_rejected` reaches 3 after three bumps).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd worker && npx vitest run test/stages.test.ts test/state.test.ts`
Expected: FAIL — no counters table, manager posting still stored as DISCOVERED and notified SEEKING.

- [ ] **Step 3: Implement**

1. In the discover loop, classify before storing:
   `const role = classifyRole(j.title, { rejectCrm: env.REJECT_CRM_ROLES !== "false" });`
   - `role.verdict === "reject"` → `upsertJob(..., { state: "PRE_FILTERED_OUT", detail: `role gate: ${role.rule}` })`,
     `bumpCounter(db, "role_rejected")`, no `EVALUATE_Q` message, and a `FILTERED` notification via the
     existing `notifyEventOnce` instead of `SEEKING`. The detail, the counter bump and the notification
     fire on a **genuine insert only**, never on a later "materially improved" re-discovery — dateless
     rows look materially improved forever, so gating on `changed` would write and count them every
     sweep. `jobs` has no reasoning column, so the detail text lives in `evaluations.reasoning`
     (audit-only today; the `FILTERED` notification carries the reason to the owner).
   - otherwise → today's behaviour unchanged (store, queue, SEEKING).
2. In `scoreJob`, before skill scoring: `const role = classifyRole(title, ...)`; on `reject` return
   `{ score: 0, eligible: false, matched: [], missing: [], reasoning: `role gate: ${role.rule}`, gated: true }`;
   on `unknown`, compute the score as today then `Math.min(score, 69)` and force `eligible = false`.
3. `env.REJECT_CRM_ROLES` is declared as an optional string on `Env` in `worker/src/index.ts` and set to
   `"true"` in `worker/wrangler.toml [vars]` (Decision 10).
4. `upsertJob`'s options object gains `state?: JobState` and `detail?: string`; when `state` is
   `PRE_FILTERED_OUT` the insert uses that state and appends the detail to the existing reasoning
   column. Existing callers keep the default `DISCOVERED`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd worker && npx vitest run test/stages.test.ts test/state.test.ts test/roles.test.ts`
Expected: PASS. Then `cd worker && npm test && npx tsc --noEmit`.

- [ ] **Step 5: Commit**

```bash
git add worker/src/index.ts worker/src/lib/evaluate.ts worker/src/state.ts worker/test/stages.test.ts worker/test/state.test.ts worker/wrangler.toml
git commit -m "feat(worker): gate manager, sales and CRM roles out of discovery and scoring"
```

---

### Task 3: One application per requisition (role-fingerprint dedupe)

**Files:**
- Modify: `worker/src/lib/hash.ts` (`roleFingerprint` currently lowercases and collapses non-alphanumerics only)
- Modify: `worker/src/state.ts` (`ensureRoleFingerprint(db)` migration; `upsertJob` stores `role_fingerprint`; new `findKnownFingerprints`)
- Modify: `worker/src/index.ts` (discover loop: batch fingerprint check before per-job upsert)
- Test: `worker/test/lib.test.ts` (normalization), `worker/test/state.test.ts` (migration + batch lookup), `worker/test/stages.test.ts` (two boards, one requisition)

**Interfaces:**
- Consumes: `bumpCounter` from Task 2.
- Produces:
  - `normalizeLocation(raw: string): string` and the updated `roleFingerprint(company: string, title: string, location: string): string` in `worker/src/lib/hash.ts`. Normalization: lowercase; collapse punctuation/whitespace; map `remote`, `anywhere`, `worldwide`, `wfh`, `work from home`, `distributed`, `telecommute`, `united states`, `us`, `usa`, `global` to the token `remote`; map empty/unknown to `?`. Hash function stays `fnv1a` (the existing helper); only normalization rules are shared with Python — cross-runtime hash equality is not required because each runtime dedupes its own store.
  - Counting discipline, carried from Task 2: anything that fires per posting (a counter bump, a detail
    write, a notification) fires on a **genuine insert only**, never on a later re-discovery. Dateless
    rows look "materially improved" forever, so gating on the jobs upsert's changed-flag over-counts.
  - `ensureRoleFingerprint(db: Db): Promise<void>` — `ALTER TABLE jobs ADD COLUMN role_fingerprint TEXT` when absent, plus `CREATE INDEX IF NOT EXISTS idx_jobs_role_fp ON jobs(role_fingerprint)`, mirroring `ensureSourceHealthColumns`.
  - `findKnownJobs(db: Db, hashes: string[], fingerprints: string[]): Promise<Map<string, string>>` — one
    query, `SELECT canonical_hash, role_fingerprint FROM jobs WHERE canonical_hash IN (...) OR
    role_fingerprint IN (...)` (chunked at 50 bindings per list), returning a map from a key (either
    identifier) to the pair stored. The caller needs both halves: a posting's **own** fingerprint is in
    the database the moment it is first stored, so a set-membership test alone would mark every
    re-discovery as a duplicate of itself. The stored `canonical_hash` for a matched fingerprint is what
    separates "this is me again" from "another board carries my requisition".

- [ ] **Step 1: Write the failing tests**

```ts
// worker/test/lib.test.ts
it("collapses remote location synonyms to one token", () => {
  expect(roleFingerprint("Acme", "Backend Engineer", "Remote, US")).toBe(
    roleFingerprint("Acme", "Backend Engineer", "United States"),
  );
});
it("keeps an unknown location distinct from remote", () => {
  expect(roleFingerprint("Acme", "Backend Engineer", "")).not.toBe(
    roleFingerprint("Acme", "Backend Engineer", "Remote"),
  );
});
it("ignores punctuation and case in title and company", () => {
  expect(roleFingerprint("Acme, Inc.", "Sr. Backend Engineer (Payments)", "Remote")).toBe(
    roleFingerprint("acme inc", "sr backend engineer payments", "remote"),
  );
});
```

```ts
// worker/test/state.test.ts
it("ensureRoleFingerprint is idempotent and indexes the column", ...);
it("findKnownFingerprints returns only fingerprints already stored", ...);
```

```ts
// worker/test/stages.test.ts
it("collapses one requisition carried by two boards into one SEEKING", async () => {
  // two sources yield the same company/title/location with different URLs
  // assert: one row in DISCOVERED, the other DUPLICATE with reasoning "duplicate of <hash>",
  // exactly one SEEKING notification, counters.duplicate_suppressed is 1
});
it("does not collapse the same title at different locations", async () => {
  // assert: two rows, both DISCOVERED
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd worker && npx vitest run test/lib.test.ts test/state.test.ts test/stages.test.ts`
Expected: FAIL — remote synonyms hash differently, no `role_fingerprint` column, both boards insert.

- [ ] **Step 3: Implement**

1. `normalizeLocation` + updated `roleFingerprint` in `hash.ts` per the interface.
2. `ensureRoleFingerprint` in `state.ts`, called once per queue batch next to the existing
   `ensureNotificationEvents` / `ensureSourceHealthColumns` calls.
3. `upsertJob` accepts `role_fingerprint: string` in its options object, includes it in the INSERT column
   list, and updates it on conflict.
4. In the discover loop, after enrichment and before the per-job loop, make the single batched query and
   derive two sets: `mine` (fingerprints whose stored `canonical_hash` equals the posting's own hash —
   this posting has been seen before) and `elsewhere` (fingerprints owned by a different hash — another
   board carries the same requisition). Per job: if `elsewhere.has(fp)`, store it with
   `state: "DUPLICATE"`, no evaluate message and no SEEKING, and bump `duplicate_suppressed` **only when
   the posting is itself newly stored** (`!mine.has(fp)`), so a sweep that re-discovers an
   already-duplicate posting costs no counter. Otherwise take today's path and add the fp to both sets,
   so two copies inside one batch collapse too. Two copies inside a batch: the first is stored normally
   and seeds the sets; the second sees its fp in `elsewhere` and is marked duplicate.
5. Strip `utm_*` and other tracking parameters in `normalizeUrl` (compare against the Python
   canonicalizer in `src/job_hunt/dedup.py` and match its behaviour) so aggregator mirrors collapse.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd worker && npx vitest run test/lib.test.ts test/state.test.ts test/stages.test.ts && npm test && npx tsc --noEmit`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worker/src/lib/hash.ts worker/src/state.ts worker/src/index.ts worker/test/lib.test.ts worker/test/state.test.ts worker/test/stages.test.ts
git commit -m "feat(worker): collapse duplicate requisitions across boards by role fingerprint"
```

---

### Task 4: A true 24-hour window on the Worker

**Files:**
- Modify: `worker/src/lib/freshness.ts` (`filterRecentList` currently keeps unknown dates; add `partitionFresh`)
- Modify: `worker/src/index.ts` (`filterRecentList(jobs, 12)` call → module constant `HOURS_OLD = 24`; dateless handling; per-source intake cap; apply-path re-gate)
- Modify: `worker/src/lib/notify.ts` (`formatJobAlert` renders source and posted age)
- Test: `worker/test/lib.test.ts` (freshness), `worker/test/stages.test.ts` (dateless, cap, apply re-gate), `worker/test/notify.test.ts` (age rendering)

**Interfaces:**
- Consumes: `classifyRole` (Task 1), `bumpCounter` (Task 2).
- Produces: `export const HOURS_OLD = 24` and
  `partitionFresh<T extends LitePosting>(postings: T[], hoursOld: number): { fresh: T[]; stale: T[]; dateless: T[] }`
  in `worker/src/lib/freshness.ts`. Task 10 reads `HOURS_OLD` for `/coverage` reporting.

- [ ] **Step 1: Write the failing tests**

```ts
// worker/test/lib.test.ts
it("splits postings into fresh, stale and dateless", () => {
  const { fresh, stale, dateless } = partitionFresh(
    [{ posted_at: new Date(Date.now() - 3600e3).toISOString() },
     { posted_at: new Date(Date.now() - 48 * 3600e3).toISOString() },
     { posted_at: "" }], 24,
  );
  expect(fresh).toHaveLength(1); expect(stale).toHaveLength(1); expect(dateless).toHaveLength(1);
});
```

```ts
// worker/test/stages.test.ts
it("stores a dateless posting without scoring, notifying or applying", async () => {
  // assert: row exists with state PRE_FILTERED_OUT and reasoning "no posting date",
  // counters.dateless_dropped is 1, no SEEKING, no evaluate message
});
it("drops postings older than 24h before upsert", ...);   // 30h-old posting never stored
it("caps a source at 30 kept postings per run, keeping the most recent", async () => {
  // source yields 45 fresh postings; assert 30 stored, the 15 most recent, counter intake_capped is 15
});
it("refuses to apply a posting that aged past 24h", async () => {
  // store an eligible row, age its posted_at, run the tailor-apply stage
  // assert: state PRE_FILTERED_OUT with a freshness detail, no application row
});
it("refuses to apply a posting whose title is a reject role", ...);  // role re-gate at apply time
```

```ts
// worker/test/notify.test.ts
it("renders the source and posted age in the alert line", () => {
  // formatJobAlert for a row posted 3h ago from "greenhouse:acme" includes "greenhouse:acme" and "3h ago"
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd worker && npx vitest run test/lib.test.ts test/stages.test.ts test/notify.test.ts`
Expected: FAIL — `partitionFresh` missing, dateless postings still notified, alert has no age.

- [ ] **Step 3: Implement**

1. `freshness.ts`: add `HOURS_OLD = 24` and `partitionFresh` (a posting with no parseable date goes to
   `dateless`, never to `fresh`).
2. `index.ts` discover stage: `const { fresh, stale, dateless } = partitionFresh(jobs, HOURS_OLD);`
   `recordSourceHealth` keeps receiving the raw count; bump `dateless_dropped` by the dateless length and
   store those rows with `state: "PRE_FILTERED_OUT"`, detail `"no posting date"`, no queue message and
   no notification. Continue from `fresh`.
3. Intake cap: after the remote filter and enrichment, sort `enriched` by `posted_at` descending and
   keep the first 30; bump `intake_capped` by the remainder.
4. Apply path: before `setJobState(db, m.hash, "TAILORED", ...)`, re-run `classifyRole(row.title)` and
   `parsePostedAt(row.posted_at)`; either failing sets `PRE_FILTERED_OUT` with the reason and sends
   `FILTERED`, then `continue` — mirroring the existing disability re-check immediately above it.
5. `notify.ts`: `formatJobAlert` appends ` · <source>` and ` · <age>` where age is rendered from
   `posted_at` as `Nh ago` / `Nm ago`; unknown age renders as `date unknown`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd worker && npx vitest run test/lib.test.ts test/stages.test.ts test/notify.test.ts && npm test && npx tsc --noEmit`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worker/src/lib/freshness.ts worker/src/index.ts worker/src/lib/notify.ts worker/test/lib.test.ts worker/test/stages.test.ts worker/test/notify.test.ts
git commit -m "feat(worker): 24h recency window, dateless drop, and apply-time re-gate"
```

---

### Task 5: The same 24-hour window in Python

**Files:**
- Modify: `config/sources.yaml` (all 62 `hours_old: 72` entries → 24)
- Modify: `src/job_hunt/orchestrator.py` (`run_discovery_stage(hours_old: int = 12)` default → 24; clamp any per-entry value above 24)
- Test: `tests/test_freshness.py`, `tests/test_discovery.py`

**Interfaces:**
- Consumes: nothing.
- Produces: the orchestrator default and clamp so no configured source can exceed 24h unless a caller
  passes an explicit lower value; `hours_old=0` still disables the window for manual runs.

- [ ] **Step 1: Write the failing tests**

```python
def test_no_configured_source_exceeds_24h():
    cfg = yaml.safe_load(Path("config/sources.yaml").read_text())
    offenders = [s["name"] for s in cfg["sources"] if (s.get("hours_old") or 0) > 24]
    assert offenders == []

def test_discovery_clamps_per_source_hours_old_to_24(monkeypatch):
    # entry with hours_old=72 passed into run_discovery_stage is clamped to 24
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_freshness.py tests/test_discovery.py -q -p no:cacheprovider`
Expected: FAIL — 62 offending sources, default is 12 and unclamped.

- [ ] **Step 3: Implement**

`sed` the 72s to 24 in `config/sources.yaml`; change the `run_discovery_stage` default to 24 and clamp
`entry["hours_old"] = min(entry.get("hours_old") or 24, 24)` where per-source hours are read, leaving
`0` (disabled) untouched.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/ -q -p no:cacheprovider`
Expected: all green (336 passed, 1 skipped baseline plus the new tests).

- [ ] **Step 5: Commit**

```bash
git add config/sources.yaml src/job_hunt/orchestrator.py tests/test_freshness.py tests/test_discovery.py
git commit -m "fix(discovery): clamp every source to a 24h recency window"
```

---

### Task 6: Python role parity (allow-first, leadership, BD, CRM)

**Files:**
- Modify: `src/job_hunt/evaluation/engine.py` (`pre_filter` order; `NEGATIVE_ROLE_PATTERNS`)
- Modify: `src/job_hunt/dedup.py` (`compute_role_fingerprint` normalization to match Task 3)
- Test: `tests/test_evaluation.py`, `tests/test_dedup.py`

**Interfaces:**
- Consumes: nothing from Tasks 1-5 (the Python rules are its own lists, mirroring `worker/src/lib/roles.ts`).
- Produces: `IC_ALLOW_PATTERNS` exported from `engine.py`; `pre_filter` evaluates the IC allow list
  before `NEGATIVE_ROLE_PATTERNS`; `compute_role_fingerprint` shares Task 3's normalization rules.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("title", [
    "Senior Manager, Application Security",
    "Engineering Team Lead, Platform",
    "Director of Software Engineering - Distributed Systems",
    "Head of Engineering",
    "Account Executive, EMEA",
    "Marketing Manager",
])
def test_pre_filter_rejects_non_ic_roles(title): ...

@pytest.mark.parametrize("title", ["Lead Software Engineer", "Staff Engineer, Platform",
                                   "Forward Deployed Engineer", "Senior Software Engineer"])
def test_pre_filter_keeps_ic_titles(title): ...

def test_pre_filter_allows_ic_title_before_checking_negatives(): ...

def test_role_fingerprint_collapses_remote_synonyms(): ...
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_evaluation.py tests/test_dedup.py -q -p no:cacheprovider`
Expected: FAIL — leadership titles pass today, remote synonyms hash differently.

- [ ] **Step 3: Implement**

Add `IC_ALLOW_PATTERNS` (same words as Task 1's IC allow row) and the new negative families (people
management, revenue, CRM behind `profile.reject_crm_roles`, defaulting to true), then reorder
`pre_filter`: sponsorship → blocked company/keywords → IC allow → negatives → positive SWE match →
clearance → location. Align `compute_role_fingerprint`'s normalization with Task 3.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/ -q -p no:cacheprovider`
Expected: green.

- [ ] **Step 5: Commit**

```bash
git add src/job_hunt/evaluation/engine.py src/job_hunt/dedup.py tests/test_evaluation.py tests/test_dedup.py
git commit -m "feat(evaluation): reject leadership, BD and CRM roles with allow-first ordering"
```

---

### Task 7: Sources carry an industry and a status

**Files:**
- Modify: `config/sources.yaml` (add `industry:` to all 382 sources; `status: live | dead | unknown`; broaden `queries`; fix the `Frontier Deployed Engineerk` typo)
- Modify: `worker/scripts/gen_sources.py` (read and emit `industry` / `status`; exclude `status: dead` sources from shards; emit `hoursOld` per source so the Worker stops hardcoding its window)
- Regenerate: `worker/src/sources.generated.ts` (via the generator, never by hand)
- Test: `tests/test_worker_source_shard.py`

**Interfaces:**
- Consumes: nothing.
- Produces: every generated source entry carries `industry` and, for live sources, `hoursOld`; `activeSources()` in `worker/src/index.ts` filters `status === "dead"` out of both cron shards and `/run`, so a dead board costs nothing. Task 8 writes `status` values from measured data.

- [ ] **Step 1: Write the failing tests**

```python
def test_every_source_declares_an_industry(): ...        # industry in {crypto, software, fintech, general}
def test_jobspy_sources_use_the_profile_vocabulary(): ... # backend, full stack, software, reactjs, nodejs, typescript, javascript, forward deployed engineer
def test_no_source_typo_engineerk(): ...
def test_generated_entries_carry_industry_and_hours(): ...
def test_dead_sources_are_excluded_from_shards(): ...    # gen_sources --check output has no dead entries
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_worker_source_shard.py -q -p no:cacheprovider`
Expected: FAIL — no `industry` field exists.

- [ ] **Step 3: Implement**

Add `industry` to every entry (crypto boards and crypto feeds → `crypto`; the major fintech/ledger
boards → `fintech`; engineering employers → `software`; aggregators and generic boards → `general`),
add `status: unknown` everywhere for now, extend every `queries` list to the profile vocabulary, fix the
typo, then teach the generator to emit `industry` and `hoursOld` and to skip `status: dead`. Run
`python worker/scripts/gen_sources.py` and confirm `--check` passes.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/ -q -p no:cacheprovider && python worker/scripts/gen_sources.py --check && cd worker && npm test && npx tsc --noEmit`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add config/sources.yaml worker/scripts/gen_sources.py worker/src/sources.generated.ts tests/test_worker_source_shard.py
git commit -m "feat(sources): industry and status per source, broader queries, dead-board filtering"
```

---

### Task 8: Probe every board, then rebuild the hourly shard from measurement

**Files:**
- Create: `scripts/probe_sources.py`
- Create (generated artifact): `data/source_probe.json`
- Modify: `config/sources.yaml` (`status:` set from the probe), regenerate `worker/src/sources.generated.ts`
- Test: `tests/test_probe_sources.py`

**Interfaces:**
- Consumes: `config/sources.yaml` sources; the product's own adapters (`src/job_hunt/discovery/`), so a probe failure means the source is dead in production too.
- Produces: `probe_all(...) -> list[SourceProbe]` and `suggest_hourly(probes, *, limit=25, max_crypto_orgs=2, max_crypto_feeds=3) -> list[str]` in `scripts/probe_sources.py`, plus the ledger `data/source_probe.json` shaped `{name: {attempts, yielded, last_yield_at, verdict, kind, industry}}` with `verdict in {live, dead, flaky}` — a uniform `name` key (the old `kind:org` stub could not key feeds).

- [ ] **Step 1: Write the failing tests**

```python
def test_probe_ledger_is_name_keyed_with_verdicts(tmp_path): ...
def test_verdict_is_live_only_with_yield_in_the_window(): ...      # 7-day window, min-attempts floor
def test_suggest_hourly_caps_crypto_orgs_and_feeds(): ...          # <=2 crypto orgs, <=3 crypto feeds
def test_suggest_hourly_includes_non_crypto_software_sources(): ...
def test_apply_flips_a_dead_source_back_to_live_when_it_yields_again(): ...


# review-focus #5 (revival) lands here: `--apply` rewrites status from the measurement,
# so a board that recovers is revived with no hand edit of generated code.
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_probe_sources.py -q -p no:cacheprovider`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `scripts/probe_sources.py`**

The script loads `config/sources.yaml`, probes each source with the existing Python adapters under the
24h/remote filters, writes `data/source_probe.json`, and prints the verdict table. Tests use synthetic
probe results; the real network run happens once, in Step 4.

- [ ] **Step 4: Run the probe, then apply its verdicts**

Run: `uv run python scripts/probe_sources.py` (dry-run output first), then
`uv run python scripts/probe_sources.py --apply && python worker/scripts/gen_sources.py`.
`--apply` rewrites each source's `status:` in `config/sources.yaml` from the measured verdict —
including flipping a recovered source back to `live` — adds up to 10 newly-yielding non-crypto
employer boards that probed `remote_fresh > 0`, and regenerates `sources.generated.ts`. Then replace
`HOURLY_ORGS`/`HOURLY_FEEDS` in `gen_sources.py` with the measured top-yield set from the ledger,
still subject to the crypto caps. Commit the ledger and both generated/config files.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_probe_sources.py tests/test_worker_source_shard.py -q -p no:cacheprovider`
Expected: green.

- [ ] **Step 6: Commit**

```bash
git add scripts/probe_sources.py tests/test_probe_sources.py data/source_probe.json config/sources.yaml worker/scripts/gen_sources.py worker/src/sources.generated.ts
git commit -m "chore(sources): measured probe ledger and a yield-ranked hourly shard"
```

---

### Task 9: One-off backfill for rows that already violate the rules

**Files:**
- Create: `scripts/backfill_feed_quality.py`
- Modify: `src/job_hunt/models.py` (`VALID_TRANSITIONS` allows `ELIGIBLE`/`TAILORED` → `PRE_FILTERED_OUT`)
- Test: `tests/test_backfill_feed_quality.py`

**Interfaces:**
- Consumes: the Python role rules from Task 6 and `filter_recent`/`is_fresh` for the 24h test.
- Produces: `main(argv)` with `--apply` (default is a dry run that prints what would change) and exit codes 0 ok / 1 nothing to do / 2 error, mirroring `scripts/pull_master.py`. Submitted rows are never touched.

- [ ] **Step 1: Write the failing tests**

```python
def test_dry_run_changes_nothing(tmp_path): ...
def test_apply_demotes_manager_rows_with_a_reason(tmp_path): ...
def test_apply_leaves_submitted_rows_alone(tmp_path): ...
def test_apply_demotes_rows_older_than_24h(tmp_path): ...
def test_apply_backfills_role_fingerprint_for_non_terminal_rows(tmp_path): ...
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_backfill_feed_quality.py -q -p no:cacheprovider`
Expected: FAIL — module missing, transition not allowed.

- [ ] **Step 3: Implement**

Query non-terminal rows, classify each with the Task 6 rules, test freshness with `is_fresh(posted_at,
24)`, and demote violators to `PRE_FILTERED_OUT` with a reason string (`role gate: <rule>` /
`stale: posted <N>h ago`). In the same pass, backfill `role_fingerprint` for every non-terminal row
from its stored company/title/location using Task 6's normalization, so the Worker dedupe is exact for
rows that already exist (spec Decision 7); submitted rows keep their history untouched. Print a
summary table in dry-run mode.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/ -q -p no:cacheprovider`
Expected: green.

- [ ] **Step 5: Commit**

```bash
git add scripts/backfill_feed_quality.py src/job_hunt/models.py tests/test_backfill_feed_quality.py
git commit -m "feat(ops): dry-run backfill that demotes pre-existing feed violations"
```

---

### Task 10: Honest coverage numbers and the docs that describe them

**Files:**
- Modify: `worker/src/index.ts` (`/coverage` and `/health` payloads)
- Modify: `README.md`, `worker/README.md`, `AGENTS.md`
- Test: `worker/test/health.test.ts`, `tests/test_docs_consistency.py`

**Interfaces:**
- Consumes: `HOURS_OLD` (Task 4), the `counters` table (Task 2).
- Produces: `/coverage` gains `dated_fresh`, `proxy_fresh` (greenhouse-family rows, i.e. sources whose
  posted_at came from `updated_at`), `dateless_dropped`, `duplicate_suppressed`, `role_rejected`, and
  `live_software` (distinct companies with at least one software-verdict posting in the last 24h).
  `/health` gains the four counters. Existing fields keep their names and meanings.

- [ ] **Step 1: Write the failing tests**

```ts
// worker/test/health.test.ts
it("/coverage reports the feed-quality counters and dated vs proxy freshness", async () => {
  // seed rows: one dated fresh, one greenhouse proxy fresh, one dateless, one DUPLICATE,
  // one role-rejected, two software companies
  // assert every new field is present with the expected counts
});
it("/health exposes role_rejected, duplicate_suppressed and dateless_dropped", ...);
```

```python
# tests/test_docs_consistency.py — extend the existing assertions
def test_docs_document_the_24h_window_and_role_gate(): ...
def test_docs_document_the_coverage_counters(): ...
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd worker && npx vitest run test/health.test.ts; uv run pytest tests/test_docs_consistency.py -q -p no:cacheprovider`
Expected: FAIL — fields absent, docs silent.

- [ ] **Step 3: Implement**

Add the aggregate queries in `index.ts` (one query for the state/counters view, one for distinct
fresh software companies within `HOURS_OLD`), then document in `worker/README.md` (role gate, dedupe,
counters, the proxy-fresh caveat, the dead/live status field and how to re-probe), `README.md` (what
the feed now guarantees), and `AGENTS.md` (the 24h window, the probe command, the backfill command).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd worker && npm test && npx tsc --noEmit; uv run pytest tests/ -q -p no:cacheprovider && python worker/scripts/gen_sources.py --check`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add worker/src/index.ts worker/test/health.test.ts README.md worker/README.md AGENTS.md tests/test_docs_consistency.py
git commit -m "docs: honest coverage counters and the feed-quality guarantees"
```

---

## Deployment note (not a task)

Code lands dark: `LIVE_APPLY=false` throughout. Deploy order for the operator is deploy → verify
`GET /health` shows the counters and `GET /coverage` shows live-software from at least 10 non-crypto
companies → run `uv run python scripts/backfill_feed_quality.py` in dry-run, review, then `--apply` →
confirm with a day's Telegram traffic that no manager/sales/duplicate/stale entry appears.
