from job_hunt.storage import Storage
from job_hunt.cv.resume_store import register_master, master_bytes


def test_resume_artifact_roundtrip(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    s.save_resume_artifact(job_id=1, kind="master", sha256="abc", storage_key="cvs/master/abc.pdf", verdict="verified", detail="")
    row = s.get_resume_artifact(1)
    assert row["kind"] == "master" and row["sha256"] == "abc"

MINIMAL_PDF = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]/Contents 4 0 R>>endobj\n"
    b"4 0 obj<</Length 44>>stream\nBT /F1 12 Tf 72 800 Td (Abdulsamed Hamdy) Tj ET\nendstream\nendobj\n"
    b"trailer<</Root 1 0 R>>\n%%EOF"
)

def test_master_registration_is_readonly(tmp_path):
    src = tmp_path / "master.pdf"
    src.write_bytes(MINIMAL_PDF)
    before = src.read_bytes()
    info = register_master(str(src))
    assert src.read_bytes() == before  # registration never modifies the file
    assert info["bytes_len"] == len(before) and info["pages"] >= 1
    assert master_bytes(str(src)) == before


import asyncio
from types import SimpleNamespace
from job_hunt.models import JobPosting, JobState
from job_hunt.orchestrator import PipelineOrchestrator

class FakeRezi:
    async def write_resume(self, payload): return {"id": "rz-1"}
    async def read_resume(self, rid): return {"name": "Acme - Backend Engineer"}
    async def download_resume_pdf(self, rid): return None  # API exposes no download
    async def aclose(self): pass

def _profile():
    # Mirror tests/test_orchestrator.py fixtures; every field build_rezi_resume_data touches.
    return SimpleNamespace(
        verified_skills=["Python"], verified_experiences=[], verified_education=[],
        verified_projects=[], summary="S", years_of_experience=4, full_name="N",
        email="e@x.com", phone="1", location="Cairo", linkedin_url=None,
        github_url=None, portfolio_url=None, allowed_metrics=[],
    )

def test_rezi_without_bytes_falls_back_to_master(tmp_path):
    storage = Storage(str(tmp_path / "t.db"))
    orch = PipelineOrchestrator(storage=storage, db_path=str(tmp_path / "t.db"))
    orch.profile = _profile()
    job = JobPosting(
        source="test", title="Backend Engineer", company="Acme",
        raw_url="https://x/1", canonical_url="https://x/1",
        canonical_url_hash="h", role_fingerprint="f", content_hash="c",
        description="Python", state=JobState.ELIGIBLE,
    )
    job, _ = storage.add_job(job)
    assert asyncio.run(orch._mirror_to_rezi_async(job, FakeRezi())) == 0
    artifact = storage.get_resume_artifact(job.id)
    assert artifact["kind"] == "master" and "no downloadable artifact" in artifact["detail"]

import pathlib

def test_no_tailored_cv_references_in_apply_path():
    apply_sources = [
        pathlib.Path("src/job_hunt/orchestrator.py").read_text(),
        pathlib.Path("src/job_hunt/automation/browser.py").read_text(),
        pathlib.Path("src/job_hunt/cli.py").read_text(),
    ]
    assert all("tailored_cv_" not in s for s in apply_sources)

def test_apply_resolves_master_when_no_artifact(tmp_path):
    import hashlib
    from job_hunt.cv.resume_store import resolve_resume_path
    master = tmp_path / "Abdulsamed_Hamdy.pdf"
    master.write_bytes(MINIMAL_PDF)
    s = Storage(str(tmp_path / "t.db"))
    assert s.get_resume_artifact(999) is None
    assert resolve_resume_path(999, s, master_path=master) == str(master.resolve())
    assert resolve_resume_path(999, s, master_path=tmp_path / "missing.pdf") is None

def test_missing_rezi_file_falls_back_to_master(tmp_path):
    import hashlib
    from job_hunt.cv.resume_store import resolve_resume_path
    master = tmp_path / "Abdulsamed_Hamdy.pdf"
    master.write_bytes(MINIMAL_PDF)
    s = Storage(str(tmp_path / "t.db"))
    s.save_resume_artifact(job_id=42, kind="rezi", sha256="x" * 64,
                           rezi_resume_id="rz-9", storage_key="cvs/rezi/rezi_42_rz-9.pdf",
                           verdict="verified", detail="")
    assert resolve_resume_path(42, s, master_path=master, cv_dir=tmp_path) == str(master.resolve())

def test_missing_master_returns_none(tmp_path):
    from job_hunt.cv.resume_store import resolve_resume_path
    s = Storage(str(tmp_path / "t.db"))
    assert resolve_resume_path(4242, s, master_path=tmp_path / "missing.pdf", cv_dir=tmp_path) is None

def test_master_sha_mismatch_returns_none(tmp_path):
    import hashlib
    from job_hunt.cv.resume_store import resolve_resume_path
    master = tmp_path / "Abdulsamed_Hamdy.pdf"
    master.write_bytes(MINIMAL_PDF)
    s = Storage(str(tmp_path / "t.db"))
    good_sha = hashlib.sha256(MINIMAL_PDF).hexdigest()
    s.save_resume_artifact(job_id=7, kind="master", sha256=good_sha,
                           storage_key="cvs/master/x.pdf", verdict="verified", detail="")
    assert resolve_resume_path(7, s, master_path=master) == str(master.resolve())
    s.save_resume_artifact(job_id=7, kind="master", sha256="0" * 64,
                           storage_key="cvs/master/x.pdf", verdict="verified", detail="")
    assert resolve_resume_path(7, s, master_path=master) is None

class FakeReziWithBytes:
    async def write_resume(self, payload): return {"id": "rz-1"}
    async def read_resume(self, rid): return {"name": "Acme - Backend Engineer"}
    async def download_resume_pdf(self, rid): return MINIMAL_PDF
    async def aclose(self): pass

def test_mirror_happy_path_resolves_to_existing_file(tmp_path, monkeypatch):
    import pathlib as _pl
    from job_hunt.cv.resume_store import resolve_resume_path
    monkeypatch.chdir(tmp_path)
    storage = Storage(str(tmp_path / "t.db"))
    orch = PipelineOrchestrator(storage=storage, db_path=str(tmp_path / "t.db"))
    orch.profile = _profile()
    job = JobPosting(
        source="test", title="Backend Engineer", company="Acme",
        raw_url="https://x/1", canonical_url="https://x/1",
        canonical_url_hash="h", role_fingerprint="f", content_hash="c",
        description="Python", state=JobState.ELIGIBLE,
    )
    job, _ = storage.add_job(job)
    assert asyncio.run(orch._mirror_to_rezi_async(job, FakeReziWithBytes())) == 1
    artifact = storage.get_resume_artifact(job.id)
    assert artifact["kind"] == "rezi"
    resolved = resolve_resume_path(job.id, storage)
    assert resolved is not None and _pl.Path(resolved).is_file()
    # Written file derives from the storage key: basenames are identical.
    assert _pl.Path(resolved).name == _pl.Path(artifact["storage_key"]).name

def test_rogue_summary_skips_mirror(tmp_path, monkeypatch):
    import job_hunt.rezi as _rezi
    storage = Storage(str(tmp_path / "t.db"))
    orch = PipelineOrchestrator(storage=storage, db_path=str(tmp_path / "t.db"))
    orch.profile = _profile()
    job = JobPosting(
        source="test", title="Backend Engineer", company="Acme",
        raw_url="https://x/2", canonical_url="https://x/2",
        canonical_url_hash="h2", role_fingerprint="f2", content_hash="c2",
        description="Python", state=JobState.ELIGIBLE,
    )
    job, _ = storage.add_job(job)
    def _rogue(_job, _profile):
        return {"name": "Acme - Backend Engineer",
                "data": {"skills": {"k": {"name": "Python"}},
                         "summary": "10x unicorn ninja with invented claims"}}
    monkeypatch.setattr(_rezi, "build_rezi_resume_data", _rogue)
    assert asyncio.run(orch._mirror_to_rezi_async(job, FakeReziWithBytes())) == 0
    assert storage.get_resume_artifact(job.id) is None

def test_mirror_skips_when_job_id_is_none(tmp_path):
    storage = Storage(str(tmp_path / "t.db"))
    orch = PipelineOrchestrator(storage=storage, db_path=str(tmp_path / "t.db"))
    orch.profile = _profile()
    job = JobPosting(
        source="test", title="Backend Engineer", company="Acme",
        raw_url="https://x/3", canonical_url="https://x/3",
        canonical_url_hash="h3", role_fingerprint="f3", content_hash="c3",
        description="Python", state=JobState.ELIGIBLE,
    )
    assert job.id is None
    assert asyncio.run(orch._mirror_to_rezi_async(job, FakeRezi())) == 0
