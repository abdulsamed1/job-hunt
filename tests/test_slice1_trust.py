from job_hunt.discovery.hosts import classify_host, is_spoof_like
from job_hunt.discovery.registry import SourceRegistry


def test_classify_host_spoof_matrix():
    assert classify_host("https://boards.greenhouse.io/stripe") == "ats"
    assert classify_host("https://jobs.ashbyhq.com/alchemy") == "ats"
    assert classify_host("https://evil-greenhouse.io/stripe") == "unverified"
    assert classify_host("https://job-boards.greenhouse.io.evil.com/x") == "unverified"
    assert classify_host("https://greenhouse.io@evil.com/x") == "unverified"
    assert classify_host("ftp://boards.greenhouse.io/x") == "unverified"
    assert classify_host("not a url") == "unverified"
    assert classify_host("https://acme.myworkdayjobs.com/jobs") == "ats"
    assert classify_host("https://evil-myworkdayjobs.com/x") == "unverified"


def test_registry_drops_spoofed_url():
    reg = SourceRegistry()
    entry = {"name": "evil", "url": "https://greenhouse.io@evil.com/x"}
    assert reg.resolve_adapter(entry) is None


def test_spoof_like_matrix():
    assert is_spoof_like("https://evil-greenhouse.io/stripe") is True
    assert is_spoof_like("https://job-boards.greenhouse.io.evil.com/x") is True
    assert is_spoof_like("https://www.linkedin.com/jobs/view/999") is False
    assert is_spoof_like("https://gitlab.com/jobs/3") is False
    assert is_spoof_like("https://boards.greenhouse.io/stripe") is False
    assert is_spoof_like("https://acme.myworkdayjobs.com/jobs") is False
    assert is_spoof_like("https://evil-myworkdayjobs.com/x") is True
