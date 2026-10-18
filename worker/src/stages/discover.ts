// The discover stage: one queue message, one source. Fetch it, keep the fresh
// remote postings inside the 24h window and under a per-source intake cap,
// store dateless ones for the record, resolve which requisition is already
// owned in one batched read, store every posting once behind the role and
// duplicate gates, and queue the ones that reach the feed for evaluation. No
// browser anywhere here, and no module-level mutable state: the queue handler
// runs messages concurrently through ctx.waitUntil, so every run builds its own
// state from the message, db and env it is handed.

import { fetchArbeitnow, fetchBdJobs, fetchJobicy, fetchJsonSearchBoard, fetchLinkedInGuest, fetchRemotive, fetchRemoteOK } from "../adapters/boards.js";
import { fetchRssFeed } from "../adapters/rss.js";
import { bamboohrListUrl, fetchBamboohr, fetchPersonio, fetchRecruitee, fetchTeamtailor, fetchWorkable, personioFeedUrl, recruiteeApiUrl, teamtailorFeedUrl, workableApiUrl } from "../adapters/tenants.js";
import { enrichDescriptions, fetchAshbyBoard, fetchAshbyIndex, fetchGreenhouseBoard, fetchIndeed, fetchLeverBoard, fetchSmartRecruitersBoard } from "../adapters/ats.js";
import type { RawJob } from "../adapters/http.js";
import { canonicalHash, roleFingerprint } from "../lib/hash.js";
import { boardUrlForDef, classifyHost } from "../lib/hosts.js";
import { detectDegraded } from "../lib/health.js";
import { filterRemoteList, HOURS_OLD, isRemoteish, parsePostedAt, partitionFresh } from "../lib/freshness.js";
import { notifyEventOnce } from "../lib/notify.js";
import { classifyRole } from "../lib/roles.js";
import { bumpCounter, findKnownJobs, recordSourceHealth, setJobState, upsertJob, type Db } from "../state.js";
import type { SourceDef } from "../sources.js";
import type { Env } from "../index.js";

/** One discover queue message: the source to fetch and store. */
export interface QueueMessage {
  stage: string;
  def: SourceDef;
}

export function linkedinEnabled(env: Env): boolean {
  return (env.LINKEDIN_ENABLED || "false").toLowerCase() === "true";
}

/** Postings one source may contribute per run. Recency orders them, so what is
 *  held back is the oldest of a chatty board, not a random slice. */
const INTAKE_CAP = 30;

/** Why a dateless posting is parked. One vocabulary, shared by the discover
 *  drop and the apply-time re-gate (see index.ts), so a row explains itself the
 *  same way whichever stage parked it. */
export const NO_DATE_DETAIL = "no posting date";

/**
 * Queue guard: fail closed on spoofed ATS hosts AND smuggled orgs/URLs. ATS
 * board URLs are rebuilt from def.org exactly as discoverSource() builds them;
 * per-tenant fetch URLs are rebuilt by the tenant builders (host-suffix +
 * scheme checks inside). A hostile org or URL in a queue message never
 * reaches fetch. Exported for tests.
 */
export function queueGuardAllows(def: SourceDef): boolean {
  if (
    def.kind === "greenhouse" || def.kind === "lever" ||
    def.kind === "ashby" || def.kind === "smartrecruiters"
  ) {
    const boardUrl = boardUrlForDef(def);
    return !!boardUrl && classifyHost(boardUrl) !== "unverified";
  }
  if (
    def.kind === "teamtailor" || def.kind === "recruitee" ||
    def.kind === "personio" || def.kind === "bamboohr" ||
    def.kind === "workable"
  ) {
    const builders = {
      teamtailor: teamtailorFeedUrl,
      recruitee: recruiteeApiUrl,
      personio: personioFeedUrl,
      bamboohr: bamboohrListUrl,
      workable: workableApiUrl,
    } as const;
    return builders[def.kind](def.url || "") !== "";
  }
  return true;
}

export async function discoverSource(def: SourceDef, env: Env): Promise<RawJob[]> {
  switch (def.kind) {
    case "rss": return fetchRssFeed(def.name, def.url || "");
    case "remoteok": return fetchRemoteOK();
    case "remotive": return fetchRemotive();
    case "arbeitnow": return fetchArbeitnow();
    case "jobicy": return fetchJobicy();
    case "greenhouse": return fetchGreenhouseBoard(def.org || "");
    case "lever": return fetchLeverBoard(def.org || "");
    case "ashby": return fetchAshbyBoard(def.org || "");
    case "smartrecruiters": return fetchSmartRecruitersBoard(def.org || "");
    case "ashby-index": return fetchAshbyIndex(def.url || "", def.maxOrgs || 8);
    case "indeed": {
      const out: RawJob[] = [];
      for (const query of def.queries || ["backend"]) {
        out.push(...(await fetchIndeed({ query, location: (def.locations || ["Cairo, Egypt"])[0], country: def.country })));
      }
      return out;
    }
    case "freehire": {
      const out: RawJob[] = [];
      for (const query of def.queries || ["backend"]) {
        for (const location of def.locations || ["Remote"]) {
          out.push(...(await fetchJsonSearchBoard({
            searchUrl: def.url || "", source: def.kind, query, location,
          })));
        }
      }
      return out;
    }
    case "bdjobs": {
      const out: RawJob[] = [];
      for (const query of def.queries || ["backend"]) {
        for (const location of def.locations || ["Bangladesh"]) {
          out.push(...(await fetchBdJobs({
            searchUrl: def.url || "", query, location,
            hoursOld: def.hoursOld ?? 24,
          })));
        }
      }
      return out;
    }
    case "teamtailor": return fetchTeamtailor(def.url || "", def.name);
    case "recruitee": return fetchRecruitee(def.url || "", def.name);
    case "personio": return fetchPersonio(def.url || "", def.name);
    case "bamboohr": return fetchBamboohr(def.url || "", def.name);
    case "workable": return fetchWorkable(def.url || "", def.name);
    case "linkedin": {
      if (!linkedinEnabled(env)) return [];
      const out: RawJob[] = [];
      for (const q of def.queries || ["backend"]) {
        out.push(...(await fetchLinkedInGuest(q, (def.locations || ["Remote"])[0], 1, def.tprSeconds || parseInt(env.TPR_SECONDS || "43200", 10))));
      }
      return out;
    }
  }
}

/** Record that this canonical hash owns the given role fingerprint (see
 *  roleFingerprint): "does another posting own this requisition?".
 *  `upsertJob` stores a posting's fingerprint, so a posting that reaches the
 *  store owns the requisition it represents. */
function noteFingerprint(index: Map<string, Set<string>>, fp: string, hash: string): void {
  const set = index.get(fp);
  if (set) set.add(hash);
  else index.set(fp, new Set([hash]));
}

/**
 * Run one discover message end to end. Deduplication reads the whole source's
 * fingerprints in a single batched call (see findKnownJobs), so a source stays
 * inside the D1 budget no matter how many postings it returns; the per-batch
 * migrations and the STAGE_FAILED handling a thrown error lands in stay with
 * the caller (queue() in index.ts).
 */
export async function runDiscoverStage(m: QueueMessage, db: Db, env: Env): Promise<void> {
  // CRM implementation roles are a Worker var, not a profile field (Decision
  // 10). Absent means on, so a deploy missing the var still filters them.
  const rejectCrm = env.REJECT_CRM_ROLES !== "false";
  // Which stored states may own a requisition, and for how long. Ownership is
  // the rule that suppresses a twin, so it is deliberately narrow: only a posting
  // that can still be applied from, and only while the opening is plausibly the
  // same one. A filtered or scored-out row owns nothing — that posting will never
  // be applied, so blocking its twin would lose the opening — and neither does a
  // row older than the window, which is how a re-posted requisition stays a new
  // requisition. The residual this leaves is deliberate and known: two genuinely
  // distinct openings at one company with the same title and place still collapse
  // into one, and once a twin exists a re-post older than the window is still
  // suppressed by it.
  const OWNER_STATES = new Set(["DISCOVERED", "ELIGIBLE", "TAILORED", "SUBMITTED"]);
  const OWNER_WINDOW_DAYS = 30;
  // The states a gate may act on: a posting whose outcome is still open — stored
  // but not yet scored (DISCOVERED), or scored and still waiting to be tailored
  // (ELIGIBLE). Every other row is left exactly as it is. One already off the feed
  // (REJECTED, PRE_FILTERED_OUT, FAILED, DUPLICATE, ...) has no opening left to
  // protect and would lose the verdict its state records; one already on its way
  // to the employer (TAILORED, SUBMITTED, APPLICATION_STARTED) carries the honest
  // history of the application, which a gate marker and its score-0 rewrite would
  // destroy. This is what makes both gate counters mean "postings kept out while
  // their outcome was still open", counted once per posting.
  const GATEABLE_STATES = new Set(["DISCOVERED", "ELIGIBLE"]);
  const def = m.def as SourceDef;
  // Fail closed on spoofed ATS hosts AND smuggled orgs/URLs
  // (see queueGuardAllows).
  // (No workday/workable/bamboohr board kinds exist on the Worker —
  // see sources.ts — so the kind list is intentionally unchanged.)
  if (!queueGuardAllows(def)) {
    console.log(`skipping source with unverified board URL: ${def.name}`);
    return;
  }
  let jobs: RawJob[] = [];
  let healthRecorded = false;
  try {
    jobs = await discoverSource(m.def, env);
  } catch (e) {
    await recordSourceHealth(db, m.def.name, m.def.kind || "", -1, -1, String(e).slice(0, 180));
    healthRecorded = true;
    throw e;
  }
  // Split, don't filter: a dateless posting is stored for the record and
  // counted (Decision 4) rather than reaching the feed, and a provably-stale
  // one is simply never stored. `stale` needs no handling beyond that.
  const { fresh, dateless } = partitionFresh(
    jobs.map((j) => ({ ...j, posted_at: j.posted_at })),
    HOURS_OLD,
  );
  const remote = filterRemoteList(fresh.map((j) => ({ ...j, location: j.location, description: j.desc, remote_flag: undefined })));
  const now = new Date().toISOString();
  // One chatty board cannot fill the run: at most INTAKE_CAP postings per
  // source reach the store, newest first. Without it a single aggregator
  // decides what the day's feed looks like and the other sources are never
  // read. The held-back postings are counted, not silently dropped — that
  // count is what says a board is being throttled.
  const kept = [...remote].sort((a, b) => (parsePostedAt(b.posted_at) ?? 0) - (parsePostedAt(a.posted_at) ?? 0)).slice(0, INTAKE_CAP);
  const heldBack = remote.length - kept.length;
  if (heldBack > 0) {
    try {
      await bumpCounter(db, "intake_capped", heldBack);
    } catch (e) {
      console.log(`intake_capped counter failed: ${String(e).slice(0, 120)}`);
    }
  }
  // Board APIs omit descriptions; enrich the few survivors so scoring
  // has real evidence instead of a title-only string.
  const enriched = await enrichDescriptions(kept);
  const degraded = detectDegraded(remote, m.def.url || "", m.def.kind || "");
  if (!healthRecorded) {
    // raw stays the board's own count: coverage measures what the source
    // answered, not what the window let through. kept is what this run could
    // actually contribute — post-cap, so the two never disagree.
    await recordSourceHealth(db, m.def.name, m.def.kind || "", jobs.length, kept.length, "", degraded);
    healthRecorded = true;
  }
  // One application per requisition: identity today is the canonical
  // URL, so the same opening arriving on two boards, or under two jids,
  // becomes two applications. Each posting therefore carries a second
  // identity — company + role + place — and the whole source's
  // fingerprints are resolved in one batched read before any of them is
  // stored, so the check never costs a query per posting.
  const postings = enriched.map((j) => ({
    job: j,
    hash: canonicalHash(j.url),
    fp: roleFingerprint(j.company, j.title, j.location),
  }));
  const datelessPostings = dateless.map((j) => ({
    job: j,
    hash: canonicalHash(j.url),
    fp: roleFingerprint(j.company, j.title, j.location),
  }));
  // One batched read covers both lists: the dateless rows are looked up for the
  // same reason the fresh ones are — a row the store already decided must not
  // be relabelled by this stage.
  const known = await findKnownJobs(
    db,
    [...postings, ...datelessPostings].map((p) => p.hash),
    [...postings, ...datelessPostings].map((p) => p.fp),
  );
  // Fingerprint -> the canonical hashes that own the requisition.
  // Ownership is bounded: only a posting that can still be applied
  // from, and only for OWNER_WINDOW_DAYS. A company re-posting the
  // same remote role next quarter is a new requisition, not a twin of
  // last quarter's row, and a row that was filtered or scored out never
  // blocks the one posting that could still be applied.
  const owners = new Map<string, Set<string>>();
  const ownerCutoff = Date.now() - OWNER_WINDOW_DAYS * 86_400_000;
  for (const row of known.values()) {
    if (!row.role_fingerprint || !OWNER_STATES.has(row.state)) continue;
    if (!(Date.parse(row.discovered_at) >= ownerCutoff)) continue;
    noteFingerprint(owners, row.role_fingerprint, row.canonical_hash);
  }
  // Dateless postings first: stored once, parked out of the feed, counted, and
  // never announced. Nothing downstream can reach them — no queue message, no
  // evaluate message, no SEEKING, no apply — so the owner's only evidence is
  // the counter. The count rides `outcome.recorded` (the reason write), not the
  // jobs upsert: a dateless row's posted_at stays empty forever, so its upsert
  // reports "materially improved" on every sweep and would count one posting
  // once per source per day.
  for (const { job: j, hash, fp } of datelessPostings) {
    const own = known.get(hash);
    if (own && !GATEABLE_STATES.has(own.state)) continue;
    const row = {
      canonical_hash: hash, title: (j.title || "").slice(0, 200),
      company: (j.company || "").slice(0, 120), location: (j.location || "").slice(0, 120),
      url: (j.url || "").slice(0, 500), description: (j.desc || "").slice(0, 4000),
      source: j.source, posted_at: "", discovered_at: now, score: 0,
      remote: isRemoteish(j.location, j.desc),
    };
    // stored first, counted second: a counter failure must never cost the row.
    const outcome = { recorded: false };
    await upsertJob(db, row, { state: "PRE_FILTERED_OUT", detail: NO_DATE_DETAIL, role_fingerprint: fp, outcome });
    if (outcome.recorded) {
      try {
        await bumpCounter(db, "dateless_dropped");
      } catch (e) {
        console.log(`dateless_dropped counter failed: ${String(e).slice(0, 120)}`);
      }
    }
  }
  for (const { job: j, hash, fp } of postings) {
    // The row the store already holds under this posting's own URL is
    // the authority on whether this posting is suppressed: a twin's
    // fingerprint can drift (the board edited the title) or lose its
    // owner, and neither may reopen the posting.
    const own = known.get(hash);
    const ownerHash = [...(owners.get(fp) || [])].find((h) => h !== hash);
    const row = {
      canonical_hash: hash, title: j.title.slice(0, 200),
      company: j.company.slice(0, 120), location: j.location.slice(0, 120),
      url: j.url.slice(0, 500), description: (j.desc || "").slice(0, 4000),
      source: j.source, posted_at: (j.posted_at || "").slice(0, 40),
      discovered_at: now, score: 0, remote: isRemoteish(j.location, j.desc),
    };
    // The alert carries the board and the posting's own age, so a feed that
    // quietly drifts older than a day is visible at a glance.
    const alert = {
      title: j.title, company: j.company, url: j.url,
      source: j.source, posted_at: j.posted_at,
    };
    // Both gates act only on postings whose outcome is still open (see
    // GATEABLE_STATES). A row the store has already decided is left
    // exactly as it is: there is no opening left to protect, and its
    // state is worth more than a gate marker — relabelling an applied
    // posting would rewrite the history of what was applied and its
    // evaluation to score 0, and counting it would report a posting the
    // owner has no remedy for. Costs no write at all.
    const gateable = !own || GATEABLE_STATES.has(own.state);
    // Role gate before storage (Decision 2): SEEKING fires here, so a
    // manager, sales or CRM posting must never reach the feed. The row
    // is still stored once, already pre-filtered with the matched rule
    // as its reason, and FILTERED replaces SEEKING.
    const role = classifyRole(j.title, { rejectCrm });
    if (role.verdict === "reject") {
      if (!gateable) continue;
      const reason = `role gate: ${role.rule}`;
      // stored first, counted second: a counter failure must never cost
      // the posting, nor the rest of this source's batch.
      const outcome = { recorded: false };
      await upsertJob(db, row, { state: "PRE_FILTERED_OUT", detail: reason, role_fingerprint: fp, outcome });
      // `recorded` (not the upsert's return value) is the first-time
      // signal: a re-discovered reject costs no count and no alert.
      if (outcome.recorded) {
        try {
          await bumpCounter(db, "role_rejected");
        } catch (e) {
          console.log(`role_rejected counter failed: ${String(e).slice(0, 120)}`);
        }
        await notifyEventOnce(db, env, "worker", hash, "FILTERED", alert, reason);
      }
      continue;
    }
    // Duplicate gate, after the role gate: a posting the role gate
    // rejected never gets here, so a rejection is explained once as a
    // rejection and never as a twin.
    if (ownerHash) {
      if (!gateable) continue;
      const reason = `duplicate of ${ownerHash}`;
      await upsertJob(db, row, { state: "DUPLICATE", detail: reason, role_fingerprint: fp });
      if (own) {
        // The jobs upsert never touches state, so a posting that was
        // already in the feed when the gate found it (every row that
        // predates the deploy) has to be moved out of it explicitly.
        await setJobState(db, hash, "DUPLICATE", reason);
        // Demoted out of the owner set. Two live rows can share one
        // fingerprint (the backfill can create that), and without this
        // the second would find the first still listed and demote it
        // too: one requisition losing both postings, counted twice.
        owners.get(fp)?.delete(hash);
      }
      // Counted on the transition only — stored and moved first, so a
      // counter failure must not cost the posting.
      try {
        await bumpCounter(db, "duplicate_suppressed");
      } catch (e) {
        console.log(`duplicate_suppressed counter failed: ${String(e).slice(0, 120)}`);
      }
      continue;
    }
    // A posting the store already parked as a duplicate stays parked
    // even when nothing owns its fingerprint any more. Without this the
    // normal path below would reopen it — and a dateless posting's row
    // counts as improved on every sweep, so it would be queued for
    // evaluation and applied a second time for one requisition.
    if (own?.state === "DUPLICATE") {
      // Refreshed text only; the upsert leaves the row's state alone.
      await upsertJob(db, row, { role_fingerprint: fp });
      continue;
    }
    const isNew = await upsertJob(db, row, { role_fingerprint: fp });
    if (isNew) {
      await env.EVALUATE_Q.send({ stage: "evaluate", hash });
      await notifyEventOnce(db, env, "worker", hash, "SEEKING", alert, `New job from ${j.source}`);
    }
    // This posting owns the requisition now, so a second copy of it
    // inside this same batch collapses too.
    noteFingerprint(owners, fp, hash);
  }
}