from api.clientkey import normalize


def test_ipv4_is_itself():
    assert normalize("203.0.113.9") == "203.0.113.9"


def test_ipv6_counted_by_64():
    assert normalize("2001:db8:1:2:aaaa::1") == normalize("2001:db8:1:2:bbbb::2")
    assert normalize("2001:db8:1:2::1") != normalize("2001:db8:1:3::1")


def test_ipv4_mapped_is_ipv4():
    assert normalize("::ffff:203.0.113.9") == "203.0.113.9"


def test_zone_id_ignored():
    assert normalize("fe80::1%en0") == normalize("fe80::1")
