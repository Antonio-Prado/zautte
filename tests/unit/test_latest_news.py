"""The latest-news job: the most recent articles of the bulletin become one known fact, injected when
the question asks for the news of now and not for the news on a subject."""
from datetime import date

from scripts import latest_news as ln

TODAY = date(2026, 11, 13)
NEWS_URL = "https://news.example.it/"
ITEMS = [{"title": f"Notizia {d}", "date": f"2026-11-{d:02d}", "url": f"{NEWS_URL}2026/11/{d:02d}/n/",
          "text": "Testo della notizia. " * 40} for d in (3, 12, 7, 10)] + [
         {"title": "Senza data", "date": "", "url": NEWS_URL + "x/", "text": "x"}]


def test_newest_first_and_only_the_dated_ones():
    news = ln.latest(ITEMS, 3)
    assert [n["title"] for n in news] == ["Notizia 12", "Notizia 10", "Notizia 7"]


def test_the_fact_lists_date_title_beginning_and_link():
    fact = ln.build_fact(ln.latest(ITEMS, 3), TODAY, "Comune di Esempio", NEWS_URL, max_text=60)
    text = fact["text"]
    assert "- giovedì 12 novembre 2026: Notizia 12. Testo della notizia." in text and "… Link: https://news.example.it/2026/11/12/n/" in text
    assert text.index("Notizia 12") < text.index("Notizia 10") < text.index("Notizia 7") and "Notizia 3" not in text
    assert "Non dire che non hai accesso alle notizie" in text and "Tutte le notizie: https://news.example.it/" in text
    assert fact["id"] == "news-latest" and fact["source"] == NEWS_URL and fact["updated"] == "2026-11-13"


def test_the_api_injects_it_for_the_news_of_now_not_for_a_subject(tmp_path, monkeypatch):
    from api import rag
    from scripts.council_sessions import upsert_fact
    path = tmp_path / "facts" / "known_facts_dynamic.json"
    fact = ln.build_fact(ln.latest(ITEMS), TODAY, "Comune di Esempio", NEWS_URL)
    assert upsert_fact(path, fact) is True
    monkeypatch.setattr(rag, "KNOWN_FACTS_DYNAMIC_FILE", path)
    monkeypatch.setattr(rag, "_DYNAMIC_FACTS", {"facts": [], "mtime": None, "checked": float("-inf")})
    titles = lambda q: [c["title"] for c in rag._inject_known_facts(q)]
    assert fact["title"] in titles("Quali sono le ultime notizie del Comune?")
    assert fact["title"] in titles("Ci sono novità?")
    assert fact["title"] in titles("Cosa è successo questa settimana in città?")
    assert fact["title"] not in titles("Ci sono notizie sulla TARI?")
    assert fact["title"] not in titles("Come posso fare la carta d'identità?")
