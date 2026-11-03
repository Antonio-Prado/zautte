import pytest

from crawler import crawler

RULES = [{"hosts": ["comune.example.org", "www.comune.example.org"], "canonical_host": "www.comune.example.org",
          "strip_path_prefix": "/index.php", "drop_query_params": ["fromService", "category_id"]},
         {"hosts": ["social.example.org"], "drop_query_params": ["category_id"]}]


@pytest.fixture(autouse=True)
def rules(monkeypatch):
    monkeypatch.setattr(crawler, "CRAWL_PAGE_ALIASES", RULES)
    monkeypatch.setattr(crawler, "CRAWL_URL_ALIASES", [{"path_prefix": "/s3/", "hosts": ["comune.example.org"],
                                                        "canonical_host": "files.example.org"}])


@pytest.mark.parametrize("url", [
    "https://comune.example.org/it/menu/216308",
    "http://www.comune.example.org/index.php/it/menu/216308",
    "https://www.comune.example.org/it/menu/216308?fromService=1",
    "https://comune.example.org/index.php/it/menu/216308?category_id=4&fromService=0",
])
def test_forms_of_a_page_become_one(url):
    assert crawler.canonical_url(url) == "https://www.comune.example.org/it/menu/216308"


def test_other_parameters_and_paths_are_kept():
    assert crawler.canonical_url("https://comune.example.org/it/news?page=2&fromService=1") == \
        "https://www.comune.example.org/it/news?page=2"
    assert crawler.canonical_url("https://www.comune.example.org/index.php") == "https://www.comune.example.org/index.php"
    assert crawler.canonical_url("https://other.example.org/index.php/x?fromService=1") == \
        "https://other.example.org/index.php/x?fromService=1"


def test_host_rule_without_canonical_host_only_drops_parameters():
    assert crawler.canonical_url("https://social.example.org/it/poi/7?category_id=3") == "https://social.example.org/it/poi/7"


def test_attachment_aliases_still_come_first():
    assert crawler.canonical_url("https://comune.example.org/s3/a.pdf") == "https://files.example.org/s3/a.pdf"


def test_cleanup_drops_a_form_only_when_its_canonical_page_is_known(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "api.privdrop", type(sys)("api.privdrop"))
    sys.modules["api.privdrop"].drop_to_sync_user = lambda: None
    from scripts import cleanup_index
    canonical = "https://www.comune.example.org/it/a"
    drop = cleanup_index.make_drop_rule({canonical, "https://comune.example.org/it/a", "https://comune.example.org/it/b"})
    assert drop("https://comune.example.org/it/a")
    assert not drop("https://comune.example.org/it/b")   # its canonical form was never fetched: kept
    assert not drop(canonical)
