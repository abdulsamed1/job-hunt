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
  return {
    score, eligible: score >= threshold, matched, missing: missing.slice(0, 10),
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
