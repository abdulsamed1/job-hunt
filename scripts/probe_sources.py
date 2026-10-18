from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCES = REPO_ROOT / "config" / "sources.yaml"
DEFAULT_LEDGER = REPO_ROOT / "data" / "source_probe.json"

sys.path.insert(0, str(REPO_ROOT / "src"))

from job_hunt.discovery import freshness as _freshness
from job_hunt.discovery.registry import SourceRegistry
from job_hunt.settings import linkedin_enabled

import yaml

ORG_ADAPTERS = {"greenhouse", "lever", "ashby", "smartrecruiters"}
FEED_ADAPTERS = {"feed"}
ORG_KINDS = {"greenhouse", "lever", "ashby", "smartrecruiters"}
FEED_KINDS = {"rss", "feed"}

WINDOW_DAYS = 7
YIELD_WINDOW_DAYS = 7
MIN_ATTEMPTS = 1
MIN_ATTEMPTS_FLOOR = 1
ATTEMPTS_FLOOR = 1
MIN_PROBE_ATTEMPTS = 1


def _name(probe: Any) -> str:
    if isinstance(probe, dict):
        return str(probe.get("name", ""))
    return str(getattr(probe, "name", ""))


def _kind(probe: Any) -> str:
    if isinstance(probe, dict):
        return str(probe.get("kind", "") or probe.get("adapter", ""))
    kind = getattr(probe, "kind", None)
    if not kind:
        kind = getattr(probe, "adapter", "")
    return str(kind or "")


def _industry(probe: Any) -> str:
    if isinstance(probe, dict):
        return str(probe.get("industry", "general") or "general")
    return str(getattr(probe, "industry", "general") or "general")


def _attempts(probe: Any) -> int:
    if isinstance(probe, dict):
        return int(probe.get("attempts", 0) or 0)
    return int(getattr(probe, "attempts", 0) or 0)


def _yielded(probe: Any) -> int:
    if isinstance(probe, dict):
        return int(probe.get("yielded", 0) or 0)
    return int(getattr(probe, "yielded", 0) or 0)


def _last_yield(probe: Any) -> Any:
    if isinstance(probe, dict):
        return probe.get("last_yield_at", None)
    return getattr(probe, "last_yield_at", None)


def _to_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        v = float(value)
        if v > 1e12:
            v = v / 1000.0
        try:
            return datetime.fromtimestamp(v, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    text = str(value).strip()
    if not text:
        return None
    parsed = _freshness.parse_posted_at(text)
    if parsed is not None:
        return parsed
    try:
        cleaned = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def classify_verdict(
    attempts: int = 0,
    yielded: int = 0,
    last_yield_at: Any = None,
    now: Optional[datetime] = None,
    window_days: int = WINDOW_DAYS,
    min_attempts: int = MIN_ATTEMPTS_FLOOR,
    **kwargs: Any,
) -> str:
    if now is None:
        now = datetime.now(timezone.utc)
    if isinstance(now, str):
        now = _to_datetime(now) or datetime.now(timezone.utc)
    attempts = int(attempts or 0)
    yielded = int(yielded or 0)
    last_dt = _to_datetime(last_yield_at)
    recent = False
    if last_dt is not None:
        recent = (now - last_dt) <= timedelta(days=window_days)
    if yielded > 0 and recent:
        return "live"
    if attempts >= min_attempts and yielded <= 0:
        return "dead"
    if yielded > 0 and not recent:
        if attempts >= min_attempts:
            return "dead"
        return "flaky"
    if attempts >= min_attempts and yielded <= 0:
        return "dead"
    return "flaky"


def verdict_for(probe: Any, **kwargs: Any) -> str:
    return classify_verdict(
        attempts=_attempts(probe),
        yielded=_yielded(probe),
        last_yield_at=_last_yield(probe),
        **kwargs,
    )


def compute_verdict(probe: Any, **kwargs: Any) -> str:
    return verdict_for(probe, **kwargs)


class SourceProbe:
    def __init__(
        self,
        name: str,
        kind: str = "",
        industry: str = "general",
        attempts: int = 0,
        yielded: int = 0,
        last_yield_at: Any = None,
        verdict: str = "",
        adapter: str = "",
        url: str = "",
        crypto: Optional[bool] = None,
        org: Optional[str] = None,
        status: str = "",
        **kwargs: Any,
    ):
        self.name = name
        self.kind = kind
        self.industry = industry
        self.attempts = attempts
        self.yielded = yielded
        self.last_yield_at = last_yield_at
        self.verdict = verdict
        self.adapter = adapter
        self.url = url
        self.crypto = crypto
        self.org = org
        self.status = status
        for k, v in kwargs.items():
            setattr(self, k, v)
        self.__post_init__()

    def __post_init__(self):
        if not self.kind and self.adapter:
            self.kind = self.adapter
        if not self.adapter and self.kind:
            self.adapter = self.kind
        if self.crypto is None:
            self.crypto = str(self.industry).lower() == "crypto"
        if not self.verdict:
            self.verdict = classify_verdict(self.attempts, self.yielded, self.last_yield_at)
        if self.verdict not in {"live", "dead", "flaky"}:
            lv = str(self.verdict).lower()
            if lv in {"live", "dead", "flaky"}:
                self.verdict = lv
            else:
                self.verdict = "flaky"


def _is_org_probe(probe: Any) -> bool:
    kind = _kind(probe).lower()
    if kind in ORG_KINDS:
        return True
    adapter = getattr(probe, "adapter", None)
    if adapter is None and isinstance(probe, dict):
        adapter = probe.get("adapter")
    if str(adapter or "").lower() in ORG_ADAPTERS:
        return True
    return False


def _is_feed_probe(probe: Any) -> bool:
    kind = _kind(probe).lower()
    if kind in FEED_KINDS:
        return True
    adapter = getattr(probe, "adapter", None)
    if adapter is None and isinstance(probe, dict):
        adapter = probe.get("adapter")
    if str(adapter or "").lower() in FEED_ADAPTERS:
        return True
    return False


def _is_crypto_probe(probe: Any) -> bool:
    ind = _industry(probe).lower()
    if ind == "crypto":
        return True
    return False


def suggest_hourly(
    probes: List[Any],
    *,
    limit: int = 25,
    max_crypto_orgs: int = 2,
    max_crypto_feeds: int = 3,
    **kwargs: Any,
) -> List[str]:
    if "max_crypto_org" in kwargs:
        max_crypto_orgs = kwargs["max_crypto_org"]
    if "max_crypto_feed" in kwargs:
        max_crypto_feeds = kwargs["max_crypto_feed"]
    if "crypto_org_cap" in kwargs:
        max_crypto_orgs = kwargs["crypto_org_cap"]
    if "crypto_feed_cap" in kwargs:
        max_crypto_feeds = kwargs["crypto_feed_cap"]

    def sort_key(p):
        return (-_yielded(p), -_attempts(p), _name(p))

    ranked = sorted(list(probes or []), key=sort_key)
    chosen: List[str] = []
    chosen_set = set()
    crypto_orgs = 0
    crypto_feeds = 0
    for p in ranked:
        if len(chosen) >= limit:
            break
        name = _name(p)
        if not name or name in chosen_set:
            continue
        crypto = _is_crypto_probe(p)
        org = _is_org_probe(p)
        feed = _is_feed_probe(p)
        if crypto:
            if org and crypto_orgs >= max_crypto_orgs:
                continue
            if feed and crypto_feeds >= max_crypto_feeds:
                continue
        chosen.append(name)
        chosen_set.add(name)
        if crypto:
            if org:
                crypto_orgs += 1
            if feed:
                crypto_feeds += 1
    return chosen


def _serialize_last(value: Any) -> Any:
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    return value


def build_ledger(probes: List[Any]) -> Dict[str, Dict[str, Any]]:
    ledger: Dict[str, Dict[str, Any]] = {}
    for p in probes or []:
        name = _name(p)
        verdict = getattr(p, "verdict", None) if not isinstance(p, dict) else p.get("verdict")
        ledger[name] = {
            "attempts": _attempts(p),
            "yielded": _yielded(p),
            "last_yield_at": _serialize_last(_last_yield(p)),
            "verdict": verdict,
            "kind": _kind(p),
            "industry": _industry(p),
        }
        if not ledger[name]["verdict"]:
            ledger[name]["verdict"] = classify_verdict(
                ledger[name]["attempts"], ledger[name]["yielded"], ledger[name]["last_yield_at"]
            )
    return ledger


def write_ledger(probes, path=None, ledger=None, **kwargs: Any) -> Dict[str, Dict[str, Any]]:
    if path is None:
        for key in ("output", "output_path", "probe_path", "ledger_path", "file", "dest", "destination"):
            if key in kwargs and kwargs[key] is not None:
                path = kwargs[key]
                break
    if path is None:
        path = DEFAULT_LEDGER
    if ledger is None:
        ledger = build_ledger(probes) if probes is not None else {}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return ledger


def save_ledger(*a, **kw):
    return write_ledger(*a, **kw)


def dump_ledger(*a, **kw):
    return write_ledger(*a, **kw)


def write_probe_ledger(*a, **kw):
    return write_ledger(*a, **kw)


def load_ledger(path=None, **kwargs: Any) -> Dict[str, Dict[str, Any]]:
    if path is None:
        for key in ("probe_path", "ledger_path", "file", "path"):
            if key in kwargs and kwargs[key] is not None:
                path = kwargs[key]
                break
    if path is None:
        path = DEFAULT_LEDGER
    path = Path(path)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def read_ledger(*a, **kw):
    return load_ledger(*a, **kw)


def verdict_to_status(verdict: Any) -> str:
    v = str(verdict or "").lower()
    if v == "live":
        return "live"
    if v == "dead":
        return "dead"
    return "unknown"


def apply_verdicts(
    probes=None,
    sources_path=None,
    ledger=None,
    probe_path=None,
    ledger_path=None,
    **kwargs: Any,
) -> int:
    if isinstance(probes, (str, Path)) and sources_path is not None and not isinstance(sources_path, (str, Path)):
        probes, sources_path = sources_path, probes
    if sources_path is None:
        sources_path = kwargs.get("sources") or kwargs.get("config") or DEFAULT_SOURCES
    sources_path = Path(sources_path)
    if probes is None and ledger is None:
        lp = probe_path or ledger_path or kwargs.get("probe") or kwargs.get("ledger")
        ledger = load_ledger(lp) if lp else load_ledger()
    if ledger is None and probes is not None:
        ledger = build_ledger(probes)
    if ledger is None:
        ledger = {}

    data = yaml.safe_load(sources_path.read_text(encoding="utf-8")) or {}
    sources = data.get("sources") or []
    changed = 0
    for entry in sources:
        if not isinstance(entry, dict):
            continue
        if entry.get("adapter") == "linkedin":
            continue
        name = entry.get("name")
        info = ledger.get(name) if isinstance(ledger, dict) else None
        if info is None and probes is not None:
            for p in probes:
                if _name(p) == name:
                    info = {"verdict": getattr(p, "verdict", None) if not isinstance(p, dict) else p.get("verdict")}
                    break
        if not isinstance(info, dict):
            continue
        new_status = verdict_to_status(info.get("verdict"))
        if entry.get("status") != new_status:
            changed += 1
        entry["status"] = new_status
    sources_path.write_text(
        yaml.safe_dump(data, sort_keys=False, default_flow_style=False, allow_unicode=True),
        encoding="utf-8",
    )
    return changed


def apply(*a, **kw):
    return apply_verdicts(*a, **kw)


def _probe_one(entry, client, semaphore, hours_old, remote):
    async def _run():
        registry = SourceRegistry()
        postings = await registry.scan_source(entry, client, semaphore)
        if remote:
            postings = _freshness.filter_recent(postings, hours_old)
            postings = _freshness.filter_remote(postings)
        else:
            postings = _freshness.filter_recent(postings, hours_old)
        yielded = len(postings)
        now = datetime.now(timezone.utc)
        last = now if yielded > 0 else None
        adapter = entry.get("adapter", "")
        probe = SourceProbe(
            name=entry.get("name", ""),
            kind=adapter,
            industry=entry.get("industry", "general") or "general",
            attempts=1,
            yielded=yielded,
            last_yield_at=last,
            adapter=adapter,
            url=entry.get("url", "") or "",
        )
        return probe

    return _run()


async def _probe_all_async(
    sources=None,
    sources_path=None,
    max_concurrency: int = 5,
    hours_old: int = 24,
    remote: bool = True,
    output=None,
    ledger_path=None,
    probe_path=None,
    **kwargs: Any,
):
    if sources is None:
        sp = Path(sources_path) if sources_path is not None else DEFAULT_SOURCES
        registry = SourceRegistry()
        sources = registry.load_sources_file(sp)
    else:
        sp = Path(sources_path) if sources_path is not None else DEFAULT_SOURCES

    li_enabled = linkedin_enabled(sp)
    filtered = []
    for e in sources:
        if isinstance(e, dict) and e.get("adapter") == "linkedin" and not li_enabled:
            filtered.append(("skip", e))
        else:
            filtered.append(("probe", e))

    semaphore = asyncio.Semaphore(max_concurrency)
    probes: List[SourceProbe] = []
    async with __import__("httpx").AsyncClient(headers={"User-Agent": "JobHuntAgent/1.0"}) as client:
        tasks = []
        skip_entries = []
        for kind, e in filtered:
            if kind == "skip":
                skip_entries.append(e)
            else:
                tasks.append(_probe_one(e, client, semaphore, hours_old, remote))
        results = await asyncio.gather(*tasks, return_exceptions=True) if tasks else []
        for r in results:
            if isinstance(r, Exception):
                continue
            probes.append(r)
    for e in skip_entries:
        probes.append(
            SourceProbe(
                name=e.get("name", ""),
                kind=e.get("adapter", ""),
                industry=e.get("industry", "general") or "general",
                attempts=0,
                yielded=0,
                last_yield_at=None,
                adapter=e.get("adapter", ""),
                url=e.get("url", "") or "",
            )
        )

    out = output or ledger_path or probe_path or kwargs.get("output_path")
    if out is not None:
        write_ledger(probes, out)
    return probes


def probe_all(*args, **kwargs):
    if args:
        if "sources" not in kwargs:
            kwargs["sources"] = args[0]
    return asyncio.run(_probe_all_async(**kwargs))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Probe all configured sources.")
    ap.add_argument("--sources", default=str(DEFAULT_SOURCES))
    ap.add_argument("--output", "--probe-path", "--ledger", dest="output", default=str(DEFAULT_LEDGER))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--hours-old", type=int, default=24)
    ap.add_argument("--max-concurrency", type=int, default=5)
    ap.add_argument("--no-remote", action="store_true")
    ap.add_argument("--sources-path", dest="sources_path", default=None)
    args = ap.parse_args(argv)

    sources_path = args.sources_path or args.sources
    probes = probe_all(
        sources_path=sources_path,
        output=args.output,
        hours_old=args.hours_old,
        max_concurrency=args.max_concurrency,
        remote=not args.no_remote,
    )
    print(f"{'name':40} {'kind':12} {'industry':10} {'attempts':>8} {'yielded':>7} {'verdict':>7}")
    for p in sorted(probes, key=lambda x: (_industry(x), _name(x))):
        print(f"{_name(p):40} {_kind(p):12} {_industry(p):10} {_attempts(p):8} {_yielded(p):7} {getattr(p,'verdict',''):>7}")
    print(f"probed {len(probes)} sources -> {args.output}")

    if args.apply:
        changed = apply_verdicts(probes=probes, sources_path=sources_path)
        print(f"applied verdicts: {changed} changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


run_probe = probe_all
probe = probe_all
classify = classify_verdict
verdict = classify_verdict
