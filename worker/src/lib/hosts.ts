import type { SourceDef } from "../sources.js";

const ATS_APEXES = [
  "boards.greenhouse.io", "boards-api.greenhouse.io",
  "jobs.lever.co", "api.lever.co",
  "jobs.ashbyhq.com", "api.ashbyhq.com",
  "jobs.smartrecruiters.com", "api.smartrecruiters.com",
  "myworkdayjobs.com",
  "workable.com",
  "bamboohr.com",
  // Stable board apexes for the remaining ATS_LINK_PATTERNS vendors
  // (browser.py): Recruitee, Jobvite, and Teamtailor all serve boards
  // under these apexes, so exact-apex matches are trusted ATS traffic.
  "recruitee.com",
  "jobvite.com",
  "teamtailor.com",
];

export function classifyHost(url: string): "ats" | "unverified" {
  let host = "";
  try {
    const u = new URL(url);
    if (u.protocol !== "http:" && u.protocol !== "https:") return "unverified";
    host = u.hostname.toLowerCase();
  } catch { return "unverified"; }
  if (!host) return "unverified";
  for (const apex of ATS_APEXES) {
    if (host === apex || host.endsWith("." + apex)) return "ats";
  }
  return "unverified";
}

// Org-derived board URLs, built exactly the way discoverSource() builds them
// (worker/src/index.ts). The discover-handler guard classifies these so an
// org smuggled in via the queue can never redirect the fetch off-board.
// NOTE: the Worker only routes greenhouse/lever/ashby/smartrecruiters by org;
// there are no workday/workable/bamboohr board kinds here, so the kind list
// below is intentionally unchanged.
const ORG_BOARD_URL_BUILDERS: Record<string, (org: string) => string> = {
  greenhouse: (org) => `https://boards-api.greenhouse.io/v1/boards/${org}/jobs`,
  lever: (org) => `https://api.lever.co/v0/postings/${org}?mode=json`,
  ashby: (org) => `https://api.ashbyhq.com/posting-api/job-board/${org}?includeCompensation=true`,
  smartrecruiters: (org) => `https://api.smartrecruiters.com/v1/companies/${org}/postings?limit=100`,
};

export const ORG_SAFE_PATTERN = /^[A-Za-z0-9_-]+$/;

export function isValidOrg(org: string): boolean {
  return ORG_SAFE_PATTERN.test(org);
}

export function boardUrlForDef(def: SourceDef): string | null {
  if (
    def.org &&
    (def.kind === "greenhouse" || def.kind === "lever" ||
      def.kind === "ashby" || def.kind === "smartrecruiters")
  ) {
    if (!isValidOrg(def.org)) return null; // fail closed on unshaped orgs
    return ORG_BOARD_URL_BUILDERS[def.kind](def.org);
  }
  return def.url || null;
}
