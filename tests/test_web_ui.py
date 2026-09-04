"""Unit and integration tests for the Web UI Dashboard endpoints."""

import pytest
from fastapi.testclient import TestClient

from job_hunt.models import JobPosting, JobState
from job_hunt.storage import Storage
from job_hunt.web.app import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    test_db = tmp_path / "test_jobs.db"
    storage = Storage(test_db)

    from job_hunt.dedup import canonical_url_hash, compute_role_fingerprint, content_hash

    # Seed test job
    job = JobPosting(
        external_id="ext-test-1",
        source="linkedin",
        source_name="LinkedIn Guest Search",
        title="Staff Backend Systems Engineer",
        company="Stripe",
        raw_url="https://www.linkedin.com/jobs/view/999999",
        canonical_url="https://www.linkedin.com/jobs/view/999999",
        canonical_url_hash=canonical_url_hash("https://www.linkedin.com/jobs/view/999999"),
        role_fingerprint=compute_role_fingerprint("Stripe", "Staff Backend Systems Engineer", "Remote Worldwide"),
        content_hash=content_hash("Seeking distributed systems engineer with Python and Kubernetes experience."),
        location="Remote Worldwide",
        description="Seeking distributed systems engineer with Python and Kubernetes experience.",
    )
    saved_job, is_new = storage.add_job(job)

    # Patch DB_PATH in web app
    monkeypatch.setattr("job_hunt.web.app.DB_PATH", str(test_db))

    return TestClient(app)


def test_index_page(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "AUTONOMOUS JOB AGENT" in response.text


def test_api_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "online"
    assert "freellmapi" in data
    assert "activity" in data


def test_api_stats(client):
    response = client.get("/api/stats")
    assert response.status_code == 200
    data = response.json()
    assert "summary" in data
    assert "daily" in data
    assert data["summary"]["DISCOVERED"] >= 1
    assert data["daily"]["daily_scan_target"] == 500


def test_api_jobs_pagination_and_filter(client):
    # Test all jobs
    response = client.get("/api/jobs?page=1&page_size=10")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1
    assert len(data["jobs"]) >= 1
    assert data["jobs"][0]["title"] == "Staff Backend Systems Engineer"

    # Test filtering by source
    res_src = client.get("/api/jobs?source=linkedin")
    assert res_src.status_code == 200
    assert len(res_src.json()["jobs"]) >= 1

    # Test search query
    res_search = client.get("/api/jobs?search=Stripe")
    assert res_search.status_code == 200
    assert len(res_search.json()["jobs"]) >= 1

    # Test search mismatch
    res_nomatch = client.get("/api/jobs?search=NonExistentCompany999")
    assert res_nomatch.status_code == 200
    assert len(res_nomatch.json()["jobs"]) == 0


def test_api_job_detail(client):
    # Get first job id
    res = client.get("/api/jobs")
    job_id = res.json()["jobs"][0]["id"]

    detail_res = client.get(f"/api/jobs/{job_id}")
    assert detail_res.status_code == 200
    data = detail_res.json()
    assert data["job"]["id"] == job_id
    assert data["job"]["company"] == "Stripe"
    assert "audit_log" in data


def test_api_logs(client, tmp_path, monkeypatch):
    log_file = tmp_path / "test.log"
    log_file.write_text("2026-09-04 [INFO] job-hunt-cli: Pipeline started\n2026-09-04 [INFO] Discovered 506 jobs")
    monkeypatch.setattr("job_hunt.web.app.LOG_PATH", str(log_file))

    res = client.get("/api/logs?lines=50")
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 2
    assert "Discovered 506 jobs" in data["lines"][1]


def test_api_sources(client):
    res = client.get("/api/sources")
    assert res.status_code == 200
    assert "sources" in res.json()
