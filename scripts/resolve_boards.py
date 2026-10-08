"""Company -> ATS board resolver (triage tooling, not runtime).

For each company name, derive a board slug (lowercased, spaces -> ``-``,
trailing Inc/LLC/etc. stripped) and probe the Greenhouse, Lever, and Ashby
public APIs in that order. The first board listing >= 1 job wins and a
``sources.yaml``-ready entry is printed. Companies that resolve nowhere print
``MANUAL: <company>`` -- they are never silently dropped.

Preview-only by default: nothing is written. ``--write FILE`` appends the
resolved YAML entries (not MANUAL lines) to FILE. Probe 404s are counted in
the dead-board memory (``data/dead_boards.json``) unless ``--no-mem``.

Usage:
    python3 scripts/resolve_boards.py --companies "Stripe,Vercel,Anthropic"
    python3 scripts/resolve_boards.py --file companies.txt --write new_sources.yaml
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from job_hunt.discovery.dead_boards import record_result  # noqa: E402

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
TIMEOUT = 20
PAUSE = 1.0
MAX_BYTES = 500_000

SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "llc", "ltd", "limited",
    "co", "company", "labs", "technologies", "technology", "gmbh", "pty",
    "plc", "sarl", "sas", "bv", "ab", "oy", "as", "aps",
}

BOARDS = (
    ("greenhouse", "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
     "https://boards.greenhouse.io/{slug}"),
    ("lever", "https://api.lever.co/v0/postings/{slug}?mode=json",
     "https://jobs.lever.co/{slug}"),
    ("ashby", "https://api.ashbyhq.com/posting-api/job-board/{slug}",
     "https://jobs.ashbyhq.com/{slug}"),
)

DEFAULT_MEM = str(Path(__file__).resolve().parent.parent / "data" / "dead_boards.json")


def slugify(company, strip_suffixes=True):
    """Lowercase, strip corporate suffixes, spaces -> hyphens.

    With ``strip_suffixes=False`` the suffix-stripping pass is skipped, giving
    the unstripped variant (e.g. ``acme-inc``) used as a resolve fallback.
    """
    tokens = re.sub(r"[.,]", "", company.lower()).split()
    if strip_suffixes:
        while tokens and tokens[-1] in SUFFIXES:
            tokens.pop()
    slug = "-".join(tokens)
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    return re.sub(r"-{2,}", "-", slug).strip("-")


def _count_jobs(board, payload):
    try:
        data = json.loads(payload)
    except ValueError:
        return 0
    if board == "lever":
        return len(data) if isinstance(data, list) else 0
    if isinstance(data, dict) and isinstance(data.get("jobs"), list):
        return len(data["jobs"])
    return 0


def default_fetch(url):
    """Probe one API URL. Returns (status, job_count)."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return (resp.status, _count_jobs(_board_for(url), resp.read(MAX_BYTES)))
    except urllib.error.HTTPError as exc:
        return (exc.code, 0)
    except Exception:
        return (0, 0)


def _board_for(url):
    for board, api_url, _ in BOARDS:
        if api_url.split("{slug}")[0] in url:
            return board
    return ""


def _probe_slug(slug, fetch):
    """Probe every board for one slug. Returns ((board, slug, count), attempts)
    where attempts is a list of (board, board_slug, status) for the memory."""
    attempts = []
    for board, api_url, _ in BOARDS:
        url = api_url.format(slug=slug)
        status, jobs = fetch(url)
        attempts.append((board, slug, status))
        count = len(jobs) if isinstance(jobs, list) else jobs
        if status == 200 and count >= 1:
            return (board, slug, count), attempts
        time.sleep(0 if fetch is not default_fetch else PAUSE)
    return None, attempts


def _remember(mem_path, attempts):
    if mem_path is None:
        return
    for board, slug, status in attempts:
        record_result(mem_path, f"{board}:{slug}", status)


def resolve_company(company, fetch=None, mem_path=None):
    """Return (board, slug, count) for the first board with >= 1 job, else None.

    Tries the suffix-stripped slug first, then the unstripped variant as a
    fallback (e.g. ``acme`` -> ``acme-inc``); 404s are recorded in the
    dead-board memory only after both variants miss, so a company that
    resolves under either spelling never pollutes the memory.
    """
    fetch = fetch or default_fetch
    slug = slugify(company)
    if not slug:
        return None
    hit, attempts = _probe_slug(slug, fetch)
    if hit is not None:
        _remember(mem_path, attempts)
        return hit
    raw = slugify(company, strip_suffixes=False)
    if raw and raw != slug:
        hit, attempts2 = _probe_slug(raw, fetch)
        attempts.extend(attempts2)
        if hit is not None:
            _remember(mem_path, attempts)
            return hit
    _remember(mem_path, attempts)
    return None


def format_result(company, resolved):
    """YAML entry for a hit, MANUAL line for a miss (never silently dropped)."""
    if resolved is None:
        return f"MANUAL: {company}"
    board, slug, _ = resolved
    board_url = next(human for name, _, human in BOARDS if name == board).format(slug=slug)
    return f"- adapter: {board}\n  name: {slug}\n  url: {board_url}"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Resolve company names to ATS boards.")
    parser.add_argument("--companies", default="",
                        help="Comma-separated company names.")
    parser.add_argument("--file", default="",
                        help="File with one company name per line.")
    parser.add_argument("--write", default="",
                        help="Append resolved YAML entries to FILE (preview-only if omitted).")
    parser.add_argument("--mem", default=DEFAULT_MEM,
                        help="Dead-board memory path.")
    parser.add_argument("--no-mem", action="store_true",
                        help="Do not update dead-board memory.")
    args = parser.parse_args(argv)

    companies = [c.strip() for c in args.companies.split(",") if c.strip()]
    if args.file:
        companies += [line.strip() for line in Path(args.file).read_text().splitlines()
                      if line.strip()]
    if not companies:
        parser.error("no companies given (use --companies or --file)")

    mem_path = None if args.no_mem else args.mem
    lines = []
    for company in companies:
        result = resolve_company(company, mem_path=mem_path)
        text = format_result(company, result)
        print(text)
        if result is not None:
            lines.append(text)

    if args.write and lines:
        with open(args.write, "a", encoding="utf-8") as fh:
            for text in lines:
                fh.write(text + "\n")


if __name__ == "__main__":
    main()
