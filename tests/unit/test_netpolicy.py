import pytest

from crawler.netpolicy import host_allowed, is_public_ip, url_allowed


@pytest.mark.parametrize("ip,ok", [
    ("93.184.216.34", True), ("2606:2800:220:1::1", True),
    ("127.0.0.1", False), ("10.1.2.3", False), ("169.254.169.254", False), ("::1", False),
    ("::ffff:127.0.0.1", False), ("64:ff9b::7f00:1", False), ("2002:7f00:1::1", False),
    ("fe80::1", False),
])
def test_public_ip(ip, ok):
    assert is_public_ip(ip) is ok


@pytest.mark.parametrize("url,ok", [
    ("https://example.org/a", True), ("https://www.example.org/a", True),
    ("https://example.org.evil.net/a", False), ("https://notexample.org/", False),
    ("javascript:alert(1)", False), ("https://user:pw@example.org/", False),
    ("https://example.org:8443/", False), ("https://127.0.0.1/", False),
])
def test_url_allowed(url, ok):
    assert url_allowed(url, ["example.org"]) is ok


def test_host_allowed_is_exact_or_subdomain():
    assert host_allowed("a.example.org", ["example.org"])
    assert not host_allowed("badexample.org", ["example.org"])
