from job_hunt.storage import Storage


def test_resume_artifact_roundtrip(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    s.save_resume_artifact(job_id=1, kind="master", sha256="abc", storage_key="cvs/master/abc.pdf", verdict="verified", detail="")
    row = s.get_resume_artifact(1)
    assert row["kind"] == "master" and row["sha256"] == "abc"
