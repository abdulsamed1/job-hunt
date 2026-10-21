// Shared fetch + parse helpers. Budgets: ≤6 concurrent connections, small
// payloads only (giant pages are skipped for the Actions pipeline instead).

export const UA =
  "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0 Safari/537.36";

export const MAX_BYTES = 1_500_000;

export async function fetchText(url: string, init?: RequestInit, maxBytes = MAX_BYTES): Promise<string | null> {
  try {
    const res = await fetch(url, {
      ...init,
      headers: { "User-Agent": UA, Accept: "application/json, text/xml, */*", ...(init?.headers || {}) },
    });
    if (!res.ok) return null;
    const buf = new Uint8Array(await res.arrayBuffer());
    if (buf.length > maxBytes) return null; // too big for 10ms CPU: skip
    return new TextDecoder().decode(buf);
  } catch {
    return null;
  }
}

export interface FetchOutcome {
  /** HTTP status, or 0 when the request itself threw (network/DNS/TLS). */
  status: number;
  /** Decoded body, or null when the request failed or the body was too big. */
  text: string | null;
}

/**
 * Like fetchText, but keeps the status. Guest endpoints (LinkedIn) throttle
 * with 429/999; a bare `null` cannot tell that apart from a network error, so
 * "why is this source failing in production?" becomes unanswerable. Used by
 * the LinkedIn guest fetcher, whose raised error carries the status.
 */
export async function fetchTextStatus(url: string, init?: RequestInit, maxBytes = MAX_BYTES): Promise<FetchOutcome> {
  try {
    const res = await fetch(url, {
      ...init,
      headers: { "User-Agent": UA, Accept: "application/json, text/xml, */*", ...(init?.headers || {}) },
    });
    if (!res.ok) return { status: res.status, text: null };
    const buf = new Uint8Array(await res.arrayBuffer());
    if (buf.length > maxBytes) return { status: res.status, text: null };
    return { status: res.status, text: new TextDecoder().decode(buf) };
  } catch {
    return { status: 0, text: null };
  }
}

export async function fetchJson(url: string, init?: RequestInit): Promise<any | null> {
  try {
    const res = await fetch(url, {
      ...init,
      headers: { "User-Agent": UA, Accept: "application/json", ...(init?.headers || {}) },
    });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

export function tagText(xml: string, tag: string): string {
  const m = xml.match(new RegExp(`<${tag}[^>]*>([\\s\\S]*?)</${tag}>`, "i"));
  if (!m) return "";
  return m[1]
    .replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1")
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export interface RawJob {
  title: string;
  company: string;
  location: string;
  url: string;
  source: string;
  posted_at: string;
  desc: string;
  salary_min?: number | null;
  salary_max?: number | null;
  salary_currency?: string | null;
  external_id?: string | null;
  remote_flag?: boolean;
}
