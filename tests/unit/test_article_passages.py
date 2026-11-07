"""«Cosa dice l'articolo 18 del regolamento?»: the passage where the article starts
(vector_store.article_passages) and the article number read from the question (rag.parse_article_query)."""
import numpy as np
import pytest

from api import rag
from indexer import vector_store as vs

PAGE = "https://example.org/it/palazzina-azzurra"
PDF = "https://files.example.org/utilizzo-palazzina-azzurra.pdf"
OTHER = "https://files.example.org/altro-regolamento.pdf"


def _meta(source, i, text, doc_type="pdf", title="Regolamento"):
    return {"source": source, "chunk_index": i, "title": title, "doc_type": doc_type,
            "text": f"{title}\n\n{text}"}


META = [
    _meta(PAGE, 0, "La Palazzina Azzurra ospita mostre. Vedi il regolamento (art. 18 e seguenti).", "html",
          "Palazzina Azzurra"),
    _meta(PDF, 0, "Art. 1 - Oggetto\nIl presente regolamento disciplina l'uso.\nArt. 2 - Finalità"),
    _meta(PDF, 1, "La partecipazione è gratuita.\nArt. 18 - Composizione\nIl Comitato è composto da:"),
    _meta(PDF, 2, "il Sindaco o suo delegato che lo presiede.\nArt. 19 - Durata"),
    _meta(PDF, 3, "Art. 180 - Non è l'articolo 18.\nArt. 18.1 nemmeno."),
    _meta(OTHER, 0, "ARTICOLO 18\nAltro regolamento, altro articolo."),
]


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setattr(vs, "_metadata", META)
    monkeypatch.setattr(vs, "_embeddings", np.zeros((len(META), 3), dtype=np.float32))
    monkeypatch.setattr(vs, "_ids", [str(i) for i in range(len(META))])
    monkeypatch.setattr(vs, "_source_rows", None)
    monkeypatch.setattr(vs, "_page_attachments", {PAGE: [PDF]})
    monkeypatch.setattr(vs, "_pdf_title_count", None)
    yield


def test_article_in_the_pdf_attached_to_the_page(store):
    found = vs.article_passages([PAGE], 18)
    assert len(found) == 1
    assert found[0]["source"] == PDF and found[0]["score"] == 1.0
    assert "Art. 18 - Composizione" in found[0]["text"]
    assert "il Sindaco o suo delegato" in found[0]["text"]   # the passage that follows comes too
    assert "Art. 180" not in found[0]["text"]


def test_article_heading_not_a_mention_nor_a_longer_number(store):
    assert vs.article_passages([PDF], 180)[0]["text"].count("Art. 180") == 1
    assert vs.article_passages([PDF], 1)[0]["text"].startswith("Regolamento\n\nArt. 1 - Oggetto")
    assert vs.article_passages([PDF], 7) == []
    assert vs.article_passages(["https://example.org/nowhere"], 18) == []


def test_article_in_every_source_in_order_up_to_the_limit(store):
    found = vs.article_passages([OTHER, PAGE], 18)
    assert [c["source"] for c in found] == [OTHER, PDF]
    assert [c["source"] for c in vs.article_passages([OTHER, PAGE], 18, limit=1)] == [OTHER]


@pytest.mark.parametrize("query, expected", [
    ("cosa dice l'articolo 18 del regolamento della palazzina azzurra?", 18),
    ("Art. 5 del regolamento edilizio", 5),
    ("ART 7 cosa prevede?", None),      # «art» without a dot or «icolo» is not an article
    ("l'articolo 18.3", None),
    ("arte contemporanea alla palazzina", None),
    ("quanto costa la mensa?", None),
])
def test_parse_article_query(query, expected):
    assert rag.parse_article_query(query) == expected
