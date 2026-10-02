"""Immutable master resume registry. Reads the file; never writes to it."""
from __future__ import annotations
import hashlib
from pathlib import Path

def register_master(pdf_path: str = "data/cvs/Abdulsamed_Hamdy.pdf") -> dict:
    raw = Path(pdf_path).read_bytes()
    pages = raw.count(b"/Type /Page") or raw.count(b"/Type/Page")
    try:
        import fitz
        with fitz.open(stream=raw, filetype="pdf") as doc:
            pages = doc.page_count
            chars = sum(len(p.get_text()) for p in doc)
    except Exception:
        chars = 0
    return {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes_len": len(raw),
        "pages": pages,
        "text_chars": chars,
    }

def master_bytes(pdf_path: str = "data/cvs/Abdulsamed_Hamdy.pdf") -> bytes:
    return Path(pdf_path).read_bytes()
