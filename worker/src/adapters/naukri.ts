// Naukri.com (India) adapter: public jobapi v3 JSON search, ported from the
// Python adapter (which followed JobSpy's endpoint usage). The request token is
// an RSA PKCS#1 v1.5 encryption of "v0|<ms>|121_<token>"; implemented with
// BigInt so no WebCrypto padding-mode support is required. No login, no
// browser, no cookies.

import { fetchTextStatus, type RawJob } from "./http.js";

const BASE_URL = "https://www.naukri.com";
const SEARCH_URL = `${BASE_URL}/jobapi/v3/search`;
const JOBS_PER_PAGE = 20;
const SEARCH_HEADERS = { appid: "109", systemid: "Naukri" };

// Public key published in JobSpy's constants (SubjectPublicKeyInfo, base64).
const NKPARAM_PUBLIC_KEY =
  "MFwwDQYJKoZIhvcNAQEBBQADSwAwSAJBALrlQ+djR0RjJwBF1xuisHmdFv334MImK6LgzJhmLhN7" +
  "B5yuEyaKoasgXQk3+OQglsOaBxEJ0j5PcTL3nbOvt80CAwEAAQ==";

function b64Bytes(text: string): Uint8Array {
  const bin = atob(text);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function bytesToB64(bytes: Uint8Array): string {
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
}

/** Read one DER TLV: returns [value, offsetAfter]. */
function readDer(data: Uint8Array, offset: number): [Uint8Array, number] {
  let length = data[offset + 1];
  let i = offset + 2;
  if (length & 0x80) {
    const size = length & 0x7f;
    length = 0;
    for (let n = 0; n < size; n++) length = length * 256 + data[i++];
  }
  return [data.slice(i, i + length), i + length];
}

function bytesToBigInt(bytes: Uint8Array): bigint {
  let value = 0n;
  for (const b of bytes) value = (value << 8n) | BigInt(b);
  return value;
}

function rsaParams(): { modulus: bigint; exponent: bigint } {
  const key = readDer(b64Bytes(NKPARAM_PUBLIC_KEY), 0)[0];
  const [, afterAlgorithm] = readDer(key, 0);
  const bitString = readDer(key, afterAlgorithm)[0];
  const rsaKey = readDer(bitString.slice(1), 0)[0];
  const [modulusBytes, afterModulus] = readDer(rsaKey, 0);
  const exponentBytes = readDer(rsaKey, afterModulus)[0];
  return { modulus: bytesToBigInt(modulusBytes), exponent: bytesToBigInt(exponentBytes) };
}

function modPow(base: bigint, exponent: bigint, modulus: bigint): bigint {
  let result = 1n;
  let b = base % modulus;
  let e = exponent;
  while (e > 0n) {
    if (e & 1n) result = (result * b) % modulus;
    b = (b * b) % modulus;
    e >>= 1n;
  }
  return result;
}

function bigIntToBytes(value: bigint, length: number): Uint8Array {
  const out = new Uint8Array(length);
  let v = value;
  for (let i = length - 1; i >= 0; i--) {
    out[i] = Number(v & 0xffn);
    v >>= 8n;
  }
  return out;
}

/** Naukri request token: RSA PKCS#1 v1.5 of "v0|<ms>|121_<token>". */
export function generateNkparam(token: string, nowMs = Date.now()): string {
  const { modulus, exponent } = rsaParams();
  const size = Math.ceil(modulus.toString(2).length / 8);
  const message = new TextEncoder().encode(`v0|${nowMs}|121_${token}`);
  const padding = new Uint8Array(size - 3 - message.length);
  crypto.getRandomValues(padding);
  for (let i = 0; i < padding.length; i++) if (padding[i] === 0) padding[i] = 1;
  const block = new Uint8Array(size);
  block[0] = 0x00;
  block[1] = 0x02;
  block.set(padding, 2);
  block[2 + padding.length] = 0x00;
  block.set(message, 3 + padding.length);
  const cipher = modPow(bytesToBigInt(block), exponent, modulus);
  return bytesToB64(bigIntToBytes(cipher, size));
}

function num(value: unknown): number | null {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function isoFromMs(ms: number): string {
  return ms ? new Date(ms).toISOString() : "";
}

export function parseNaukriJob(job: any, hoursOld: number | null, nowMs = Date.now()): RawJob | null {
  if (!job || typeof job !== "object") return null;
  const created = num(job.createdDate) || 0;
  if (hoursOld && created && created < nowMs - hoursOld * 3600_000) return null;
  const title = String(job.title || "").trim();
  const jdUrl = String(job.jdURL || "").trim();
  if (!title || !jdUrl) return null;

  const labels: Record<string, string> = {};
  for (const label of job.placeholders || []) {
    if (label && label.type) labels[String(label.type)] = String(label.label || "");
  }
  const rawLocation = labels.location || "";
  const sep = rawLocation.lastIndexOf(" - ");
  let workMode = "";
  let city = rawLocation;
  if (sep >= 0) {
    workMode = rawLocation.slice(0, sep);
    city = rawLocation.slice(sep + 3);
  }
  if (city === "Remote") workMode = "Remote";
  if (city === "Remote" || city === "India") city = "";
  const isRemote = workMode === "Remote";
  let location = city || "India";
  if (isRemote) location = city ? `${city} (Remote)` : "Remote";

  const company = String(job.companyName || "Naukri Employer");
  const skills = String(job.tagsAndSkills || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  let desc = `${title} position at ${company}. Location: ${location}.`;
  if (skills.length) desc += ` Skills: ${skills.join(", ")}.`;
  const experience = String(job.experienceText || "");
  if (experience) desc += ` Experience: ${experience}.`;
  desc += " Discovered via Naukri.";

  const salary = job.salaryDetail || {};
  const hasSalary = !salary.hideSalary && num(salary.minimumSalary) !== null && num(salary.maximumSalary) !== null;

  return {
    title,
    company,
    location,
    url: BASE_URL + jdUrl,
    source: "naukri",
    posted_at: isoFromMs(created),
    desc,
    salary_min: hasSalary ? num(salary.minimumSalary) : null,
    salary_max: hasSalary ? num(salary.maximumSalary) : null,
    salary_currency: hasSalary ? String(salary.currency || "INR") : null,
    remote_flag: isRemote,
    external_id: job.jobId ? `nk-${job.jobId}` : null,
  };
}

export async function fetchNaukri(opts: {
  query: string;
  location: string;
  hoursOld: number | null;
  remoteOnly?: boolean;
  page?: number;
}): Promise<RawJob[]> {
  const page = opts.page || 1;
  const params = new URLSearchParams({
    noOfResults: String(JOBS_PER_PAGE),
    urlType: "search_by_keyword",
    searchType: "adv",
    keyword: opts.query || "",
    pageNo: String(page),
  });
  if (opts.location) params.set("location", opts.location);
  if (opts.hoursOld) params.set("jobAge", String(Math.ceil(opts.hoursOld / 24)));
  if (opts.remoteOnly) params.set("wfhType", "2");

  const res = await fetchTextStatus(`${SEARCH_URL}?${params.toString()}`, {
    headers: {
      ...SEARCH_HEADERS,
      nkparam: generateNkparam("srp"),
      "Accept-Language": "en-US,en;q=0.9",
      Accept: "application/json",
    },
  });
  if (res.status === 0) throw new Error("Naukri: network error");
  if (res.status !== 200) throw new Error(`Naukri: HTTP ${res.status}`);
  if (res.text === null) throw new Error("Naukri: response too large");

  let data: any;
  try {
    data = JSON.parse(res.text);
  } catch {
    throw new Error("Naukri: unparseable response");
  }
  const jobs = Array.isArray(data?.jobDetails) ? data.jobDetails : [];
  const out: RawJob[] = [];
  for (const job of jobs) {
    const parsed = parseNaukriJob(job, opts.hoursOld);
    if (parsed) out.push(parsed);
    if (out.length >= JOBS_PER_PAGE) break;
  }
  return out;
}
