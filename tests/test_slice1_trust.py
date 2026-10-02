from job_hunt.discovery.hosts import classify_host


def test_classify_host_spoof_matrix():
    assert classify_host("https://boards.greenhouse.io/stripe") == "ats"
    assert classify_host("https://jobs.ashbyhq.com/alchemy") == "ats"
    assert classify_host("https://evil-greenhouse.io/stripe") == "unverified"
    assert classify_host("https://job-boards.greenhouse.io.evil.com/x") == "unverified"
    assert classify_host("https://greenhouse.io@evil.com/x") == "unverified"
    assert classify_host("ftp://boards.greenhouse.io/x") == "unverified"
    assert classify_host("not a url") == "unverified"
