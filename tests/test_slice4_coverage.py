from job_hunt.discovery import robots


def test_robots_parsing_rules():
    assert robots.group_allows("User-agent: *\nDisallow: /jobs\n", "/jobs", "TestBot") is False
    assert robots.group_allows("User-agent: *\nDisallow: /jobs\n", "/about", "TestBot") is True
    assert robots.group_allows("", "/", "TestBot") is None  # empty/unreadable is not permission
