"""indexed_by_news_sync (crawler/crawler.py): the crawler leaves the news site to scripts/news_sync.py."""

from crawler import crawler


def test_pages_of_the_news_host_are_left_to_the_news_sync(monkeypatch):
    monkeypatch.setattr(crawler, "_NEWS_HOST", "bum.example.it")
    assert crawler.indexed_by_news_sync("https://bum.example.it/2026/07/13/eventi-entro-il-31-luglio/")
    assert not crawler.indexed_by_news_sync("https://turismo.example.it/evento/concordia-stagione-26-27/")
    assert not crawler.indexed_by_news_sync("https://www.example.it/it/news/1868608")


def test_without_a_news_source_every_page_is_indexed(monkeypatch):
    monkeypatch.setattr(crawler, "_NEWS_HOST", "")
    assert not crawler.indexed_by_news_sync("https://bum.example.it/2026/07/13/eventi-entro-il-31-luglio/")
