"""One-off recon: probe candidate boards for unauthenticated jobs + stable IDs.

Triage only, not a sweep: single-digit requests per board, 20s timeouts,
browser UA, small read caps, ~1s courtesy pause between requests.

Usage: python3 scripts/recon_boards.py
Exits 0 after printing one JSON verdict line per probe.
"""

import json
import re
import time
import urllib.request
import xml.etree.ElementTree as ET

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
HEADERS = {"User-Agent": UA,
           "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                     "application/json;q=0.8,*/*;q=0.7",
           "Accept-Language": "en-US,en;q=0.9"}
TIMEOUT = 20
MAX_BYTES = 200_000
PAUSE = 1.0

# (candidate, label, url, kind) in brief priority order.
TARGETS = [
    # 1. Teamtailor global API (expected: needs token -> NO-GO, documents why
    #    per-tenant RSS is the path instead).
    ("teamtailor", "global-api", "https://api.teamtailor.com/v1/jobs?page%5Bsize%5D=5", "json"),
    # 1b. Teamtailor per-tenant public RSS (ref: career-ops providers/teamtailor.mjs).
    # Tenants confirmed via websearch: Teamtailor's own board + Software Finder
    # (multi-level subdomain — note for adapter host-regex).
    ("teamtailor", "rss/career (own board)", "https://career.teamtailor.com/jobs.rss", "rss"),
    ("teamtailor", "rss/softwarefinder.na", "https://softwarefinder.na.teamtailor.com/jobs.rss", "rss"),
    # 2. Recruitee per-tenant offers API (ref: providers/recruitee.mjs).
    # Tenants confirmed via websearch (live boards with tech openings).
    ("recruitee", "make", "https://make.recruitee.com/api/offers/", "json"),
    ("recruitee", "happeo", "https://happeo.recruitee.com/api/offers/", "json"),
    ("recruitee", "radix", "https://radix.recruitee.com/api/offers/", "json"),
    # 3. Personio per-tenant XML feed (ref: providers/personio.mjs).
    # vivid confirmed via websearch (Vivid fintech board, tech roles).
    ("personio", "vivid", "https://vivid.jobs.personio.de/xml", "xml"),
    ("personio", "finn", "https://finn.jobs.personio.de/xml", "xml"),
    # 4. Pinpoint per-tenant postings.json (ref: providers/pinpoint.mjs).
    # workwithus = Pinpoint's own board on *.pinpointhq.com.
    ("pinpoint", "workwithus (own board)", "https://workwithus.pinpointhq.com/postings.json", "json"),
    ("pinpoint", "clearbank", "https://clearbank.pinpointhq.com/postings.json", "json"),
    # 5. iCIMS hosted search pages (ref: providers/icims.mjs).
    # Tenants confirmed via websearch (live /jobs/search portals).
    ("icims", "centricbrands", "https://careers-centricbrands.icims.com/jobs/search?ss=1&pr=0&in_iframe=1", "html"),
    ("icims", "nv5", "https://careers-nv5.icims.com/jobs/search?ss=1&pr=0&in_iframe=1", "html"),
    # 6. JobIndex hydrated state (ref: jobindex-search skill helpers.ts).
    ("jobindex", "search/python-7d", "https://www.jobindex.dk/jobsoegning?q=python&jobage=7", "html"),
    # 7. Jobbank.dk RSS (ref: jobbank-search skill url-reference.md).
    ("jobbank", "rss/python", "https://jobbank.dk/job/rss?key=python", "rss"),
]


import urllib.error

def fetch(url):
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return {"http": r.status, "body": r.read(MAX_BYTES),
                    "ctype": r.headers.get("Content-Type", "")}
    except urllib.error.HTTPError as e:
        try:
            body = e.read(MAX_BYTES)
        except Exception:
            body = b""
        return {"http": e.code, "body": body,
                "ctype": e.headers.get("Content-Type", ""),
                "error": f"HTTPError {e.code}"}
    except Exception as e:
        return {"http": None, "body": b"", "error": str(e)[:150]}


def summarize(kind, body):
    """Return (jobs, id_field, keys_note) or ({},) on unparseable."""
    text = body.decode("utf-8", "replace")
    if kind == "json":
        data = json.loads(text)
        if isinstance(data, dict):
            for arr_key in ("offers", "data", "jobs", "positions", "results"):
                arr = data.get(arr_key)
                if isinstance(arr, list):
                    first = arr[0] if arr else {}
                    keys = sorted(first.keys()) if isinstance(first, dict) else []
                    id_field = next((k for k in
                                     ("id", "careers_url", "url", "path", "slug", "tid")
                                     if k in keys), None)
                    return len(arr), id_field, f"{arr_key}[] keys={keys[:8]}"
            return 0, None, f"dict keys={sorted(data.keys())[:8]} (no job array)"
        if isinstance(data, list):
            return len(data), None, "top-level list"
        return 0, None, f"unexpected json type {type(data).__name__}"
    if kind in ("rss", "xml"):
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            root = None
        if root is not None:
            items = root.findall(".//item") or root.findall(".//{*}item")
            if items:
                has_link = any(it.find("link") is not None for it in items[:5])
                return len(items), ("link" if has_link else None), "rss <item>"
            pos = root.findall(".//position")
            if pos:
                ids = [p.findtext("id") for p in pos[:5]]
                return len(pos), ("id" if any(ids) else None), "personio <position>"
            return 0, None, f"xml root=<{root.tag}> no item/position"
        # Fallback (mirrors ref provider parsePersonioXml): descriptions embed
        # raw markup that breaks a naive whole-doc XML parse, so strip
        # <jobDescriptions> subtrees and split <position> blocks by regex.
        stripped = re.sub(r"<jobDescriptions\b[^>]*>[\s\S]*?</jobDescriptions>",
                          "", text, flags=re.I)
        blocks = re.findall(r"<position\b[^>]*>[\s\S]*?</position>", stripped)
        if blocks:
            ids = re.findall(r"<id>(\d+)</id>", " ".join(blocks[:20]))
            return len(blocks), ("id" if ids else None), \
                f"personio <position> via description-stripped split (sample ids={ids[:3]})"
        return 0, None, "xml unparseable even after description strip"
    if kind == "html":
        if "var Stash = " in text:  # jobindex hydrated state
            m = re.search(r'"results"\s*:\s*\[', text)
            tids = set(re.findall(r'"tid"\s*:\s*"([^"]+)"', text[:MAX_BYTES]))
            return len(tids), ("tid" if tids else None), "jobindex Stash blob"
        cards = len(re.findall(r"iCIMS_JobCardItem", text))
        if cards:
            hrefs = set(re.findall(r"/jobs/(\d+)/", text))
            return cards, ("job-id-in-href" if hrefs else None), "icims cards"
        jobitems = len(re.findall(r"job-item", text))
        return jobitems, None, "html (no known job shape)"
    return 0, None, "unknown kind"


def probe(candidate, label, url, kind):
    res = fetch(url)
    if res.get("http") is None:
        return {"candidate": candidate, "label": label, "url": url,
                "http": None, "error": res.get("error")}
    try:
        jobs, id_field, note = summarize(kind, res["body"])
    except Exception as e:
        return {"candidate": candidate, "label": label, "url": url,
                "http": res["http"], "error": f"parse: {str(e)[:100]}"}
    out = {"candidate": candidate, "label": label, "url": url,
           "http": res["http"], "jobs": jobs,
           "stable_id": id_field, "shape": note,
           "bytes": len(res["body"]), "content_type": res["ctype"][:60]}
    # GO needs: HTTP 200 + job array + stable ID + no auth (all these URLs are
    # unauthenticated by construction; a 401/403 is an auth wall -> NO-GO).
    out["verdict"] = ("GO" if res["http"] == 200 and jobs > 0 and id_field
                      else "NO-GO")
    return out


if __name__ == "__main__":
    import sys
    only = (sys.argv[sys.argv.index("--only") + 1]
            if "--only" in sys.argv else None)
    go_candidates: set = set()
    for i, (candidate, label, url, kind) in enumerate(TARGETS):
        if only and candidate != only:
            continue
        if i and not only:
            time.sleep(PAUSE)
        # Bound scope per brief: candidates run in priority order; once 3
        # distinct candidates are GO, remaining candidates are DEFERRED
        # (no further requests issued for them).
        if len(go_candidates) >= 3 and candidate not in go_candidates:
            print(json.dumps({"candidate": candidate, "label": label,
                              "url": url, "verdict": "DEFERRED",
                              "reason": "scope bound: 3 GOs already banked"}))
            continue
        result = probe(candidate, label, url, kind)
        if result.get("verdict") == "GO":
            go_candidates.add(candidate)
        print(json.dumps(result))
