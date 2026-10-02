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
