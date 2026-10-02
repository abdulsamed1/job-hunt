"""Immutable master resume registry. Reads the file; never writes to it."""
from __future__ import annotations
import hashlib
from pathlib import Path
from typing import Optional

MASTER_PDF_PATH = Path("data/cvs/Abdulsamed_Hamdy.pdf")
CV_DIR = Path("data/cvs")

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


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve_resume_path(
    job_id: Optional[int],
    storage=None,
    *,
    master_path: Optional[str | Path] = None,
    cv_dir: Optional[str | Path] = None,
) -> Optional[str]:
    """Resolve the resume file for a job: verified Rezi variant, else master.

    Fail-closed: returns None when the chosen file does not exist (the rezi
    leg falls through to the master check; the master leg returns None when
    missing). For kind==master rows the file sha256 must additionally match
    the recorded sha; a mismatch is treated as missing.
    """
    base = Path(cv_dir) if cv_dir is not None else CV_DIR
    master = Path(master_path) if master_path is not None else MASTER_PDF_PATH
    artifact = None
    if storage is not None and job_id is not None:
        try:
            artifact = storage.get_resume_artifact(job_id)
        except Exception:
            artifact = None
    if artifact and artifact.get("kind") == "rezi" and artifact.get("storage_key"):
        candidate = base / Path(artifact["storage_key"]).name
        if candidate.is_file():
            return str(candidate.resolve())
        # Fall through to the master check below.
    if artifact and artifact.get("kind") == "master":
        if not master.is_file():
            return None
        recorded = artifact.get("sha256") or ""
        if recorded and _sha256_file(master) != recorded:
            return None
        return str(master.resolve())
    if master.is_file():
        return str(master.resolve())
    return None
