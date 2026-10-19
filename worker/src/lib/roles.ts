// Role classifier: what kind of job is this title?
//
// The feed is software-only, so a title is bucketed into "software" (keep),
// "reject" (people management, revenue, CRM implementation, non-engineering) or
// "unknown" (no signal either way). Pure string work — no I/O, no env, no
// clock: the caller owns the rejectCrm switch because it lives in a Worker var.

export type RoleVerdict = "software" | "reject" | "unknown";

export interface RoleClassification {
  verdict: RoleVerdict;
  /** Stable slug of the matched rule, e.g. "people-management:manager". Never empty. */
  rule: string;
}

export interface ClassifyRoleOptions {
  /** Reject Salesforce/CRM implementation roles. Defaults to true. */
  rejectCrm?: boolean;
}

interface Rule {
  /** Slug under the family prefix; several surface forms can share one slug. */
  slug: string;
  /** Word-boundary matched forms of the rule, e.g. ["team lead", "group lead"]. */
  words: string[];
}

interface Match {
  rule: string;
  word: string;
}

/** Lowercase with punctuation flattened to spaces, whitespace collapsed. Dots stay
 *  inside words so "node.js" survives as one token; edge dots ("Sr.") are dropped.
 *  Letters, marks and digits of any script survive — this feed reads MENA, Egypt
 *  and India boards, so an accented title must keep its words. */
function normalizeWords(title: string): string[] {
  return String(title ?? "")
    .toLowerCase()
    .replace(/[^\p{L}\p{M}\p{N}.]+/gu, " ")
    .split(" ")
    .map((w) => w.replace(/^\.+|\.+$/g, ""))
    .filter(Boolean);
}

/** Whole-word match: "sales" never matches "salesforce". */
function hasWord(norm: string, word: string): boolean {
  return ` ${norm} `.includes(` ${word} `);
}

function hasAnyWord(wordSet: Set<string>, candidates: string[]): boolean {
  return candidates.some((w) => wordSet.has(w));
}

function collectMatches(norm: string, prefix: string, rules: Rule[]): Match[] {
  const matches: Match[] = [];
  for (const rule of rules) {
    for (const word of rule.words) {
      if (hasWord(norm, word)) matches.push({ rule: `${prefix}:${rule.slug}`, word });
    }
  }
  return matches;
}

/** Most specific match wins: the longest matched word, first listed on a tie. A
 *  titled specialist beats a generic label, so "Marketing Manager" is marketing
 *  and "Customer Success Manager" is customer success, while "Senior Manager,
 *  Application Security" has only "manager" to go on. */
function mostSpecific(matches: Match[]): Match | null {
  let best: Match | null = null;
  for (const m of matches) {
    if (!best || m.word.length > best.word.length) best = m;
  }
  return best;
}

// (1) IC allow: seniority that marks individual-contributor work. It stands in
// for the software allow-list, never for the reject families: "Senior Sales
// Engineer" is a sales role and "Senior Engineering Manager" is a manager
// role, whatever the seniority marker says. "lead engineer" is matched as
// written so "Team Lead, Backend Engineering" stays a reject.
const IC_PHRASES = ["lead engineer", "lead software engineer"];
const SENIORITY = ["staff", "principal", "senior", "sr"];
const ENGINEERING_NOUNS = ["engineer", "engineers", "engineering"];

function isIcRole(norm: string, wordSet: Set<string>): boolean {
  if (IC_PHRASES.some((p) => hasWord(norm, p))) return true;
  return hasAnyWord(wordSet, SENIORITY) && hasAnyWord(wordSet, ENGINEERING_NOUNS);
}

// (2) Reject families. Salesforce implementation needs an anchor plus one of the
// companions, so "Salesforce Consultant" rejects while a bare "Salesforce" does not.
const PEOPLE_MANAGEMENT: Rule[] = [
  { slug: "manager", words: ["manager"] },
  { slug: "team lead", words: ["team lead", "group lead"] },
  { slug: "head of", words: ["head of"] },
  { slug: "director", words: ["director"] },
  { slug: "vp", words: ["vp"] },
  { slug: "chief", words: ["chief"] },
  { slug: "supervisor", words: ["supervisor"] },
];

const REVENUE: Rule[] = [
  { slug: "sales", words: ["sales"] },
  { slug: "sales development", words: ["sales development"] },
  { slug: "business development", words: ["business development"] },
  { slug: "account executive", words: ["account executive"] },
  { slug: "account manager", words: ["account manager"] },
  { slug: "marketing", words: ["marketing"] },
  { slug: "partner", words: ["partner"] },
  { slug: "customer success", words: ["customer success"] },
  { slug: "customer support", words: ["customer support"] },
];

const CRM_ANCHOR = "salesforce";
const CRM_COMPANIONS = ["consultant", "architect", "administrator", "analyst", "developer"];
const CRM_MATCH: Match = { rule: "crm:salesforce", word: CRM_ANCHOR };

const NON_ENGINEERING: Rule[] = [
  { slug: "recruiter", words: ["recruiter", "talent acquisition"] },
  { slug: "human resources", words: ["human resources"] },
  { slug: "accountant", words: ["accountant"] },
  { slug: "finance", words: ["finance"] },
  { slug: "legal", words: ["legal"] },
  { slug: "medical", words: ["nurse", "medical"] },
  { slug: "driver", words: ["driver"] },
  { slug: "content writer", words: ["content writer"] },
  { slug: "designer", words: ["graphic designer"] },
  // "intern" and "internship" are the same thing: a board that writes
  // "Software Engineering Internship" must not slip past as a software role
  // (Python rejects both, see evaluation/engine.py).
  { slug: "intern", words: ["intern", "internship"] },
  { slug: "product", words: ["product manager"] },
];

const REJECT_FAMILIES: Array<{ prefix: string; rules: Rule[] }> = [
  { prefix: "people-management", rules: PEOPLE_MANAGEMENT },
  { prefix: "revenue", rules: REVENUE },
  { prefix: "non-engineering", rules: NON_ENGINEERING },
];

// (3) Software allow. A technology name only counts when the title also names the
// build role, so "JavaScript" in a stack blurb does not qualify on its own.
const SOFTWARE_ALLOW: Rule[] = [
  { slug: "software", words: ["software", "software engineer", "software developer"] },
  { slug: "backend", words: ["backend"] },
  { slug: "frontend", words: ["frontend", "front end"] },
  // "fullstack" is the same word as "full stack": Python's `\bfull\s*stack\b`
  // accepts both spellings, so the Worker must too.
  { slug: "full stack", words: ["full stack", "fullstack"] },
  { slug: "platform", words: ["platform engineer"] },
  { slug: "site reliability", words: ["site reliability", "sre"] },
  { slug: "devops", words: ["devops"] },
  { slug: "cloud", words: ["cloud engineer"] },
  { slug: "infrastructure", words: ["infrastructure"] },
  { slug: "distributed systems", words: ["distributed systems"] },
  { slug: "systems", words: ["systems engineer"] },
  { slug: "data", words: ["data engineer"] },
  { slug: "machine learning", words: ["ml engineer", "machine learning"] },
  { slug: "mobile", words: ["mobile", "ios", "android"] },
  { slug: "qa", words: ["qa", "sdet", "test engineer"] },
  { slug: "security", words: ["security engineer"] },
  { slug: "embedded", words: ["embedded", "firmware"] },
  { slug: "forward deployed", words: ["forward deployed engineer"] },
];

const TECHNOLOGIES = [
  "nodejs", "node.js", "typescript", "react", "reactjs", "javascript", "python", "golang", "rust", "java",
];
const BUILD_ROLES = ["engineer", "engineers", "engineering", "developer", "developers"];

export function classifyRole(title: string, opts: ClassifyRoleOptions = {}): RoleClassification {
  const words = normalizeWords(title);
  const norm = words.join(" ");
  const wordSet = new Set(words);

  // The reject families come first, and nothing overrides them. A revenue, CRM
  // or non-engineering word beats any seniority marker ("Senior Sales
  // Engineer" is a sales role, "Senior Marketing Engineer" is a marketing
  // role), and a people-management word beats it too ("Senior Engineering
  // Manager" is a manager role). The IC allow used to short-circuit ahead of
  // these families, which let revenue titles into the feed.
  const rejects: Match[] = [];
  for (const family of REJECT_FAMILIES) rejects.push(...collectMatches(norm, family.prefix, family.rules));
  if (opts.rejectCrm !== false && hasWord(norm, CRM_ANCHOR) && hasAnyWord(wordSet, CRM_COMPANIONS)) {
    rejects.push(CRM_MATCH);
  }
  const rejected = mostSpecific(rejects);
  if (rejected) return { verdict: "reject", rule: rejected.rule };

  // The IC allow stands in for the software allow-list below: a seniority
  // marker plus an engineering noun is individual-contributor work even when
  // the title names no technology ("Staff Engineer, Platform").
  if (isIcRole(norm, wordSet)) {
    return { verdict: "software", rule: "software:ic" };
  }

  const allows = collectMatches(norm, "software", SOFTWARE_ALLOW);
  const technology = TECHNOLOGIES.find((t) => wordSet.has(t));
  if (technology && hasAnyWord(wordSet, BUILD_ROLES)) allows.push({ rule: "software:stack", word: technology });
  const allowed = mostSpecific(allows);
  if (allowed) return { verdict: "software", rule: allowed.rule };

  return { verdict: "unknown", rule: "unknown:none" };
}
