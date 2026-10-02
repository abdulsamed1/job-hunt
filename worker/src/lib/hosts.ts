const ATS_APEXES = [
  "boards.greenhouse.io", "boards-api.greenhouse.io",
  "jobs.lever.co", "api.lever.co",
  "jobs.ashbyhq.com", "api.ashbyhq.com",
  "jobs.smartrecruiters.com", "api.smartrecruiters.com",
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
