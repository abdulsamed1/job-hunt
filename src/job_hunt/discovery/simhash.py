"""64-bit SimHash over 3-token shingles. Under-signal returns None, never a zero hash."""
from __future__ import annotations
import hashlib
import re

def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())

def simhash64(text: str) -> int | None:
    toks = _tokens(text)
    if len(" ".join(toks)) < 200 or len(toks) < 3:
        return None
    acc = [0] * 64
    for i in range(len(toks) - 2):
        h = int.from_bytes(hashlib.sha1(" ".join(toks[i:i+3]).encode()).digest()[:8], "big")
        for b in range(64):
            acc[b] += 1 if (h >> b) & 1 else -1
    out = 0
    for b, v in enumerate(acc):
        if v > 0:
            out |= 1 << b
    return out

def _similarity(x: int, y: int) -> float:
    return 1.0 - bin(x ^ y).count("1") / 64.0

def is_cross_listing(a: dict, b: dict) -> bool:
    if set(a["title"].lower().split()) != set(b["title"].lower().split()):
        return False
    if a["company"].lower() == b["company"].lower() or a["url"] == b["url"]:
        return False
    ha, hb = simhash64(a["text"]), simhash64(b["text"])
    if ha is None or hb is None:
        return False
    return _similarity(ha, hb) >= 0.92
