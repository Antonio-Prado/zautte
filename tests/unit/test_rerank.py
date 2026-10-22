from api import rag


def _chunk(title, score=0.70, **meta):
    return {"title": title, "score": score, "source": meta.pop("source", "https://www.example.org/" + title), **meta}


def test_news_and_retired_domain_come_after_a_page_of_equal_relevance():
    page = _chunk("Servizio mensa", category="servizio")
    news = _chunk("Mensa, al via le iscrizioni", category="notizia")
    attachment = _chunk("Avviso mensa", category="allegato", doc_type="html")
    retired = _chunk("Mensa scolastica", source="https://old.example.org/mensa")
    ranked = rag.rerank([retired, attachment, news, page], "pasti")   # no query term in titles
    assert [c["title"] for c in ranked] == ["Servizio mensa", "Mensa scolastica", "Avviso mensa",
                                            "Mensa, al via le iscrizioni"]


def test_a_clearly_more_relevant_news_item_stays_first():
    page = _chunk("Servizio mensa", score=0.60, category="servizio")
    news = _chunk("Allerta meteo per domani", score=0.75, category="notizia")
    assert rag.rerank([page, news], "allerta")[0]["title"] == "Allerta meteo per domani"
