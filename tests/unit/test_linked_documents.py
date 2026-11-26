"""A question that carries the link of a document of the index («posso partecipare a questo bando?
https://…&dettaglio=7») gets that document whole, looked up by its address: sources_of_links and
whole_documents (indexer/vector_store.py), _linked_sources and retrieve_context (api/rag.py)."""
import numpy as np
import pytest

from api import rag
from indexer import vector_store as vs

CALL = "https://portal.example.org/?page_id=36046&voceamt=544&dettaglio=7"
TITLE = "BANDO DI SELEZIONE PER LE PROGRESSIONI TRA LE AREE"
PAGE = "https://www.example.org/it/page/tari"
PLAN = "https://example.org/files/piano.pdf"


def _passage(source, title, index, body, **more):
    return {"source": source, "title": title, "doc_type": "pdf", "category": "documento",
            "chunk_index": index, "text": f"{title}\n\n{body}", **more}


META = [
    _passage(CALL, TITLE, 1, "REQUISITI PER L'AMMISSIONE: anzianità minima di 3 anni."),
    _passage(PAGE, "TARI", 0, "La tassa sui rifiuti."),
    _passage(CALL, TITLE, 0, "Sezione Bandi di concorso. Allegati: bando.pdf.", date="2026-11-25"),
    _passage(CALL, TITLE, 2, "Il termine di scadenza è VENERDI' 4 DICEMBRE 2026."),
    *[_passage(PLAN, "Piano", i, f"Capitolo {i}. " + "x" * 90) for i in range(6)],
]
# the passages of the plan closest to the query vector [1, 0, 0]: the 4th, then the 2nd
VECTORS = np.zeros((len(META), 3), dtype=np.float32)
VECTORS[:, 2] = 1.0
VECTORS[4 + 4] = [1.0, 0.0, 0.0]
VECTORS[4 + 2] = [0.8, 0.0, 0.6]


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setattr(vs, "_metadata", META)
    monkeypatch.setattr(vs, "_embeddings", VECTORS)
    monkeypatch.setattr(vs, "_ids", [str(i) for i in range(len(META))])
    monkeypatch.setattr(vs, "_source_rows", None)
    monkeypatch.setattr(vs, "_source_keys", None)
    monkeypatch.setattr(vs, "_copy_of", None)
    monkeypatch.setattr(vs, "_pdf_title_count", None)
    yield


def test_a_link_is_looked_up_by_address(store):
    assert vs.sources_of_links([CALL]) == {CALL: CALL}
    # with or without www., http or https, the final slash, a fragment the index does not know
    for written in ("http://example.org/it/page/tari/", "https://WWW.example.org/it/page/tari#orari"):
        assert vs.sources_of_links([written]) == {written: PAGE}
    assert vs.sources_of_links(["https://example.org/it/page/imu", CALL.replace("=7", "=8")]) == {}


def test_a_short_document_is_given_whole_in_its_order(store):
    [doc] = vs.whole_documents([CALL, CALL])
    assert doc["source"] == CALL and doc["score"] == 1.0 and doc["partial"] is False
    assert doc["date"] == "2026-11-25"
    assert doc["text"] == (f"{TITLE}\n\nSezione Bandi di concorso. Allegati: bando.pdf.\n"
                           "REQUISITI PER L'AMMISSIONE: anzianità minima di 3 anni.\n"
                           "Il termine di scadenza è VENERDI' 4 DICEMBRE 2026.")
    assert vs.whole_documents(["https://example.org/unknown"]) == []


def test_of_a_long_document_the_first_passage_and_the_closest_ones(store):
    [doc] = vs.whole_documents([PLAN], [1.0, 0.0, 0.0], max_chars=330)
    assert doc["partial"] is True and len(doc["text"]) <= 330
    chapters = [line.split(".")[0] for line in doc["text"].splitlines() if line.startswith("Capitolo")]
    assert chapters == ["Capitolo 0", "Capitolo 2", "Capitolo 4"]
    assert doc["text"].count("[…]") == 3   # 1, 3 and 5 left out
    # without a query vector, the first passages
    [doc] = vs.whole_documents([PLAN], None, max_chars=330)
    assert "Capitolo 1" in doc["text"] and "Capitolo 2" in doc["text"] and "Capitolo 4" not in doc["text"]
    # the budget is shared among the documents
    both = vs.whole_documents([CALL, PLAN], None, max_chars=660)
    assert [d["partial"] for d in both] == [False, True]


def test_the_question_without_the_links_found(store):
    assert rag._linked_sources(f"posso partecipare a questo bando? {CALL}") == ("posso partecipare a questo bando?", [CALL])
    assert rag._linked_sources(f"{CALL}.") == ("", [CALL])                      # nothing but the link
    unknown = "cosa dice https://example.org/it/page/imu ?"
    assert rag._linked_sources(unknown) == (unknown, [])                         # not in the index: left as it was
    assert rag._linked_sources("quanto costa la TARI?") == ("quanto costa la TARI?", [])


def test_the_linked_document_comes_first_and_the_search_gets_the_words(store, monkeypatch):
    searched = []

    def search(vec, text, **kw):
        searched.append(text)
        return [{"text": "TARI\n\nLa tassa sui rifiuti.", "source": PAGE, "title": "TARI", "score": 0.7},
                {"text": META[0]["text"], "source": CALL, "title": TITLE, "score": 0.6}]

    monkeypatch.setattr(rag, "_KNOWN_FACTS", [])
    monkeypatch.setattr(rag, "_dynamic_known_facts", list)
    monkeypatch.setattr(rag, "embed_query", lambda text: [1.0, 0.0, 0.0])
    monkeypatch.setattr(rag, "hybrid_search", search)
    monkeypatch.setattr(rag, "ATTACHMENT_PASSAGES", 0)
    chunks = rag.retrieve_context(f"posso partecipare a questo bando? {CALL}")
    assert searched == ["posso partecipare a questo bando?"]
    assert [c["source"] for c in chunks] == [CALL, PAGE]      # the passage of the same document found by the search is dropped
    first = chunks[0]
    assert first["known"] is True and "partial" not in first
    assert first["text"].startswith("(Documento della pagina indicata con un link nella domanda.)\n" + TITLE)
    assert "anzianità minima di 3 anni" in first["text"] and "4 DICEMBRE 2026" in first["text"]
    # listed under the answer even when the answer does not write the link
    shown = rag.shown_sources("Sì, se sei dipendente di ruolo.", [{"title": TITLE, "url": CALL, "known": True, "links": []}])
    assert shown == [{"title": TITLE, "url": CALL}]
