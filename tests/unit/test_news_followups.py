"""A news item that ends what an earlier one announced (a ban revoked) is put right after it."""
import numpy as np
import pytest

from api import rag
from indexer import vector_store as vs

BAN = "https://news.example.it/2026/07/09/balneazione-vietata-sentina/"
LIFTED = "https://news.example.it/2026/07/13/mare-di-nuovo-balneabile/"


def _news(source, title, day, text, category="notizia", chunk=0):
    return {"source": source, "title": title, "doc_type": "html", "chunk_index": chunk, "origin": "news",
            "category": category, "date": day, "text": text}


META = [
    _news(BAN, "Escherichia coli superiore ai limiti, balneazione vietata in un punto della Sentina", "2026-07-09",
          "Notizia del 9 luglio 2026. Il sindaco ha firmato l'ordinanza di divieto di balneazione."),
    _news("https://news.example.it/2026/07/10/festa/", "Torna la festa della Marina", "2026-07-10",
          "Notizia del 10 luglio 2026. Di nuovo in piazza la festa, con la processione in mare."),
    _news(LIFTED, "Il mare della Sentina è di nuovo balneabile", "2026-07-13",
          "Notizia del 13 luglio 2026. Il sindaco ha revocato l'ordinanza con cui si disponeva il divieto di "
          "balneazione: i valori di Escherichia coli sono ora inferiori ai limiti di legge."),
    _news(LIFTED, "Il mare della Sentina è di nuovo balneabile", "2026-07-13", "Secondo brano.", chunk=1),
    _news("https://news.example.it/2027/07/20/revoca/", "Revocato il divieto di balneazione alla Sentina per Escherichia coli",
          "2027-07-20", "Notizia del 20 luglio 2027. Revocato il divieto: Escherichia coli nei limiti."),
    _news("https://www.example.it/page", "Balneazione alla Sentina: limiti di Escherichia coli", "2026-07-14",
          "Pagina: revocato.", category="servizio"),
]


@pytest.fixture
def store(monkeypatch):
    vecs = np.ones((len(META), 3), dtype=np.float32)
    monkeypatch.setattr(vs, "_metadata", META)
    monkeypatch.setattr(vs, "_embeddings", vecs / np.linalg.norm(vecs, axis=1, keepdims=True))
    monkeypatch.setattr(vs, "_ids", [str(i) for i in range(len(META))])
    for name in ("_versions", "_grants", "_source_rows", "_pdf_title_count", "_news", "_copy_of"):
        monkeypatch.setattr(vs, name, None)
    yield


def chosen(*rows):
    return [{**META[i], "score": 0.7} for i in rows]


def test_the_revocation_follows_the_ban(store):
    found = vs.news_followups(chosen(0))
    assert list(found) == [BAN] and found[BAN]["source"] == LIFTED and found[BAN]["date"] == "2026-07-13"
    out = rag._with_followups(chosen(1, 0))
    assert [c["source"] for c in out] == [META[1]["source"], BAN, LIFTED]


def test_nothing_is_added_when_it_is_already_there_or_unrelated(store):
    assert vs.news_followups(chosen(0, 2)) == {}          # the revocation is already among the passages
    assert vs.news_followups(chosen(1)) == {}             # «di nuovo» alone, nothing shared with the title
    assert vs.news_followups(chosen(2)) == {}             # the revocation of a year later is not its follow-up
    assert rag._with_followups(chosen(1)) == chosen(1)
