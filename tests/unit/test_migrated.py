from config.settings import is_migrated_source


def test_host_and_subdomains():
    assert is_migrated_source("https://old.example.org/x")
    assert is_migrated_source("https://a.old.example.org/x")


def test_mention_in_query_or_lookalike_is_not_migrated():
    assert not is_migrated_source("https://www.example.org/?u=old.example.org")
    assert not is_migrated_source("https://notold.example.org/")


def test_path_prefix_on_whole_segments():
    assert is_migrated_source("https://portal.example.org/legacy/a.pdf")
    assert is_migrated_source("https://portal.example.org/legacy")
    assert not is_migrated_source("https://portal.example.org/legacyX")
    assert not is_migrated_source("https://portal.example.org/other")
