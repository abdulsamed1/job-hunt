// Honest option mapping (mirrors Python match_answer_to_option).

const NEGATION = new Set(["no", "not", "never", "n't", "decline", "declines", "declined", "disagree", "none"]);
const DECLINE_HINTS = ["decline", "prefer not", "choose not", "don't wish", "do not wish", "not disclose"];
const YES_WORDS = new Set(["yeah", "yep", "yea", "affirmative"]);
const NO_WORDS = new Set(["nope", "nah", "negative"]);

function foldVariants(text: string): string {
  return text
    .split(/\b/)
    .map((w) => (YES_WORDS.has(w.toLowerCase()) ? "yes" : NO_WORDS.has(w.toLowerCase()) ? "no" : w))
    .join("");
}

function words(text: string): Set<string> {
  return new Set((text.toLowerCase().match(/[a-z0-9]+/g) || []));
}

function polarity(text: string): boolean | null {
  const w = words(text);
  for (const n of NEGATION) if (w.has(n)) return false;
  for (const y of ["yes", "agree", "accept", "confirm"]) if (w.has(y)) return true;
  return null;
}

export function matchAnswerToOption(answer: string, options: string[]): string | null {
  if (!answer || !options || options.length === 0) return null;
  const normAns = foldVariants(answer.trim());
  const ansWords = words(normAns);
  const ansPolarity = polarity(normAns);

  for (const opt of options) {
    if (foldVariants(opt.trim()) === normAns) return opt;
  }
  if (DECLINE_HINTS.some((d) => normAns.includes(d))) {
    for (const opt of options) {
      if (DECLINE_HINTS.some((d) => opt.toLowerCase().includes(d))) return opt;
    }
    return null;
  }
  for (const opt of options) {
    const normOpt = foldVariants(opt.trim());
    const optWords = words(normOpt);
    if (ansWords.size === 0 || optWords.size === 0) continue;
    const sub = [...ansWords].every((w) => optWords.has(w)) || [...optWords].every((w) => ansWords.has(w));
    if (!sub) continue;
    const optPolarity = polarity(normOpt);
    if (ansPolarity !== null && optPolarity !== null && ansPolarity !== optPolarity) continue;
    if (DECLINE_HINTS.some((d) => normOpt.includes(d))) continue;
    return opt;
  }
  return null;
}

const ATTEST = ["certify", "attest", "swear", "penalty of perjury", "under penalty", "authorize a background", "authorize background", "background check", "drug test", "drug testing", "authorize a drug"];
const ROUTINE = ["privacy policy", "terms of", "terms and conditions", "consent to processing", "process my data", "data processing"];

export function checkboxAction(label: string): "check" | "skip" {
  const lbl = (label || "").toLowerCase();
  if (!lbl.trim()) return "skip";
  if (ATTEST.some((p) => lbl.includes(p))) return "skip";
  if (ROUTINE.some((p) => lbl.includes(p))) return "check";
  return "skip";
}
