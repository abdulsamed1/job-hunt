// Deterministic fit scoring + hard gates (mirrors Python evaluation/engine.py).

export interface Profile {
  verifiedSkills: string[];
  yearsOfExperience: number;
  openToRemote: boolean;
  sponsorshipRequired: boolean;
  citizenship?: string | null;
  authorizedCountries: string[];
  blockedCompanies: string[];
  blockedKeywords: string[];
  preferredKeywords: string[];
  location: string;
  languages: string[];
}

export interface GateVerdict {
  verdict: "PASS" | "FAIL" | "UNVERIFIED";
  quote?: string;
}

const CITIZENSHIP_FAIL = [
  /must be (?:a )?citizens?(?: of [^.]+)?/i,
  /citizenship (?:in|of) [a-z ]+ required/i,
  /(?:permanent\s+residen\w+|full working rights).{0,40}required/i,
  /only [a-z ]*citizens (?:may|can) apply/i,
  /security clearance/i,
];

const KNOWN_LANGUAGES = [
  "english", "danish", "norwegian", "swedish", "german", "french", "spanish",
  "portuguese", "italian", "dutch", "polish", "arabic", "hindi", "chinese",
  "mandarin", "japanese", "korean", "russian", "ukrainian", "turkish",
];

// Disability-targeting gate: a FAIL requires (a) a `disabilit*` mention in the
// title, (b) a program noun (incl. plurals) within ±80 chars of a description
// mention, or (c) a direct disability hiring/employment phrase. Encouragement
// sentences without program/hiring nouns are suppressed; bare mentions (EEO /
// reasonable-accommodation boilerplate) still pass.
const DISABILITY_PHRASE = /disabilit\w*/gi;
const PROGRAM_NOUN = /\b(programs?|initiatives?|tracks?|internships?|fellowships?|cohorts?)\b/i;
const HIRING_NOUN = /\b(hiring|recruiting|recruitment|employment)\b/i;
const ENCOURAGEMENT = /encourag\w*|welcom|invit/i;
const DISABILITY_HIRING_PHRASE =
  /disabilit\w*[\s-]+(?:hiring|employment|recruit(?:ment|ing))|(?:hiring|recruit(?:ment|ing)|employment)\s+(?:(?:of|for)\s+)?(?:(?:people|persons|individuals|candidates|applicants|employees|talent)\s+)?with\s+disabilit\w*/i;

// Sentence breaks need punctuation followed by whitespace (or end of text), so
// abbreviations and decimals ("e.g.", "3.5") never split a sentence.
const SENTENCE_BREAK = /[.!?](?=\s|$)|\n/g;

/** Bounds of the sentence containing [start, end). */
function sentenceBounds(text: string, start: number, end: number): [number, number] {
  let sentStart = 0;
  let sentEnd = text.length;
  // Fresh regex per call: /g matching is stateful (lastIndex).
  const re = new RegExp(SENTENCE_BREAK.source, "g");
  let b: RegExpExecArray | null;
  while ((b = re.exec(text)) !== null) {
    if (b.index + b[0].length <= start) sentStart = b.index + b[0].length;
    else if (b.index >= end) { sentEnd = b.index; break; }
  }
  return [sentStart, sentEnd];
}

/** Slice `text` around [start, end) so the matched phrase is never cut away. */
function clip(text: string, start: number, end: number, radius = 100): string {
  return text.slice(Math.max(0, start - radius), Math.min(text.length, end + radius)).trim();
}

export function checkDisabilityTargetingGate(title: string, description: string): GateVerdict {
  const t = title || "";
  const d = description || "";
  const titleMatch = new RegExp(DISABILITY_PHRASE.source, "i").exec(t);
  if (titleMatch) {
    return { verdict: "FAIL", quote: clip(t, titleMatch.index, titleMatch.index + titleMatch[0].length) };
  }
  const phrase = new RegExp(DISABILITY_PHRASE.source, DISABILITY_PHRASE.flags);
  let m: RegExpExecArray | null;
  while ((m = phrase.exec(d)) !== null) {
    const [s, e] = sentenceBounds(d, m.index, m.index + m[0].length);
    const sentence = d.slice(s, e);
    // Pure encouragement boilerplate is suppressed only while it carries no
    // program/hiring noun of its own.
    if (ENCOURAGEMENT.test(sentence) && !PROGRAM_NOUN.test(sentence) && !HIRING_NOUN.test(sentence)) continue;
    const window = d.slice(Math.max(0, m.index - 80), m.index + m[0].length + 80);
    if (PROGRAM_NOUN.test(window)) {
      return { verdict: "FAIL", quote: clip(d, m.index, m.index + m[0].length) };
    }
  }
  const hiringPhrase = DISABILITY_HIRING_PHRASE.exec(d);
  if (hiringPhrase) {
    return { verdict: "FAIL", quote: clip(d, hiringPhrase.index, hiringPhrase.index + hiringPhrase[0].length) };
  }
  return { verdict: "PASS" };
}

export function checkEligibilityGate(description: string): GateVerdict {
  const desc = description || "";
  for (const p of CITIZENSHIP_FAIL) {
    const m = desc.match(p);
    if (m) return { verdict: "FAIL", quote: m[0].trim() };
  }
  return { verdict: "UNVERIFIED" };
}

export function checkLanguageGate(title: string, description: string, profile: Profile): GateVerdict {
  const langs = new Set((profile.languages || []).map((s) => s.toLowerCase()));
  if (langs.size === 0) return { verdict: "UNVERIFIED" };
  const text = `${title} ${description}`;
  const tech = new Set((profile.verifiedSkills || []).map((s) => s.toLowerCase()));
  const required: string[] = [];
  // re-scan per language to collect ALL requirements (multilingual-safe)
  for (const lang of KNOWN_LANGUAGES) {
    if (tech.has(lang)) continue;
    const patterns = [
      new RegExp(`fluent ${lang}\\b`, "i"),
      new RegExp(`native ${lang}\\b`, "i"),
      new RegExp(`${lang} (?:fluency|required|mandatory)\\b`, "i"),
      new RegExp(`must speak ${lang}\\b`, "i"),
      new RegExp(`communicate .{0,40} in ${lang}\\b`, "i"),
    ];
    if (patterns.some((p) => p.test(text))) required.push(lang);
  }
  if (required.length === 0) return { verdict: "PASS" };
  const missing = required.filter((l) => !langs.has(l));
  if (missing.length > 0) return { verdict: "FAIL", quote: missing[0] };
  return { verdict: "PASS" };
}

export interface ScoreResult {
  score: number;
  eligible: boolean;
  matched: string[];
  missing: string[];
  reasoning: string;
  gated: boolean;
}

const TECH_VOCAB = new Set([
  "python", "go", "rust", "java", "typescript", "javascript", "react", "node",
  "django", "fastapi", "kubernetes", "docker", "aws", "terraform", "sql",
  "postgresql", "redis", "kafka", "linux", "graphql",
]);

export function scoreJob(
  title: string, description: string, location: string, profile: Profile, threshold = 70,
): ScoreResult {
  const gate0 = checkDisabilityTargetingGate(title, description);
  if (gate0.verdict === "FAIL") {
    return { score: 0, eligible: false, matched: [], missing: [], reasoning: `Disability-targeted posting: ${gate0.quote}`, gated: true };
  }
  const gate1 = checkEligibilityGate(description);
  if (gate1.verdict === "FAIL") {
    return { score: 0, eligible: false, matched: [], missing: [], reasoning: `Eligibility gate FAIL: ${gate1.quote}`, gated: true };
  }
  const gate2 = checkLanguageGate(title, description, profile);
  if (gate2.verdict === "FAIL") {
    return { score: 0, eligible: false, matched: [], missing: [], reasoning: `Language gate FAIL: ${gate2.quote}`, gated: true };
  }
  const text = `${title} ${description}`.toLowerCase();
  const matched: string[] = [];
  const missing: string[] = [];
  const verified = new Map((profile.verifiedSkills || []).map((s) => [s.toLowerCase(), s]));
  for (const [low, orig] of verified) {
    if (new RegExp(`\\b${low.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`).test(text)) matched.push(orig);
  }
  for (const kw of TECH_VOCAB) {
    if (new RegExp(`\\b${kw}\\b`).test(text) && !matched.map((m) => m.toLowerCase()).includes(kw)) missing.push(kw);
  }
  const total = matched.length + missing.length;
  const skillScore = total > 0 ? (matched.length / total) * 55 : 30;
  const seniorityScore = 25; // years parsing lives server-side; neutral on worker
  const loc = (location || "").toLowerCase();
  const isRemote = /remote|anywhere|worldwide|global/.test(loc);
  const locationScore = isRemote ? 20 : profile.openToRemote ? 15 : 10;
  const score = Math.min(100, Math.round((skillScore + seniorityScore + locationScore) * 10) / 10);
  // Worker runs unsupervised: empty-signal generosity is a footgun (a title
  // with zero skill evidence must never go eligible on participation points).
  const eligible = score >= threshold && matched.length >= 1;
  return {
    score, eligible, matched, missing: missing.slice(0, 10),
    reasoning: `serverless score ${score} (skills ${skillScore.toFixed(1)}, seniority ${seniorityScore}, location ${locationScore})`,
    gated: false,
  };
}

export function classifyTier(title: string, description = ""): string {
  const t = (title || "").toLowerCase();
  if (/\bintern(?:ship)?\b/.test(t)) return "intern";
  if (/\bjunior\b|\bentry\b|\bgraduate\b|\bassociate\b/.test(t)) return "entry";
  if (/\bstaff\b|\bprincipal\b|\bmanager\b|\bdirector\b|\blead\b/.test(t)) return "lead";
  if (/\bsenior\b|\bsr\.?\b/.test(t)) return "senior";
  const m = (description || "").toLowerCase().match(/(\d+)\+?\s*years?/);
  if (m && parseInt(m[1], 10) >= 8) return "senior";
  return "mid";
}
