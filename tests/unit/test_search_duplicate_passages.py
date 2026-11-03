import numpy as np
import pytest

from indexer import vector_store as vs


@pytest.fixture
def store(monkeypatch):
    """A tiny store: one page indexed under four addresses, two other pages."""
    news = "Contributi a familiari di persone ospiti in strutture residenziali"
    rows = [(news, "https://www.example.org/it/news/contributi"),
            (news, "https://example.org/it/news/contributi"),
            (news, "https://www.example.org/index.php/it/news/contributi"),
            (news, "https://www.example.org/it/news/contributi?type=3"),
            ("Sostegno alle donne con familiari ospiti in strutture residenziali", "https://www.example.org/it/news/donne"),
            ("Contributi alle famiglie per le rette di ricovero in strutture", "https://www.example.org/it/news/rette")]
    vectors = {news: [1.0, 0.0, 0.0], rows[4][0]: [0.9, 0.3, 0.0], rows[5][0]: [0.8, 0.0, 0.4]}
    metadata = [{"text": text, "source": src, "title": text, "doc_type": "html"} for text, src in rows]
    emb = np.array([vectors[text] for text, _ in rows], dtype=np.float32)
    monkeypatch.setattr(vs, "_embeddings", emb / np.linalg.norm(emb, axis=1, keepdims=True))
    monkeypatch.setattr(vs, "_metadata", metadata)
    monkeypatch.setattr(vs, "_ids", [str(i) for i in range(len(rows))])
    monkeypatch.setattr(vs, "_bm25", vs._make_bm25(metadata))
    monkeypatch.setattr(vs, "_bm25_dirty", False)
    monkeypatch.setattr(vs, "_copy_of", None)


def test_copies_of_a_passage_do_not_take_the_place_of_other_pages(store):
    found = vs.hybrid_search([1.0, 0.0, 0.0], "contributi familiari strutture residenziali", top_k=3)
    assert [c["source"].rsplit("/", 1)[1] for c in found][1:] == ["donne", "rette"]
    assert found[0]["source"].split("?")[0].endswith("/it/news/contributi")


@pytest.fixture
def copies(monkeypatch):
    """One regulation published as three files (two in another folder), and another document."""
    reg = ["Art. 1 Oggetto del regolamento edilizio", "Art. 2 Interventi di manutenzione ordinaria"]
    urls = ["https://files.example.org/allegati/regolamento.pdf",
            "https://files.example.org/allegati/pnrr/2-regolamento.pdf",
            "https://files.example.org/allegati/pnrr/6-regolamento.pdf"]
    rows = [(t, u, i) for u in urls for i, t in enumerate(reg)]
    rows.append(("Manutenzione delle aree verdi pubbliche", "https://files.example.org/verde.pdf", 0))
    # the indexer puts the cached file name first, different for every copy
    metadata = [{"text": f"{u.rsplit('/', 1)[1]}_9e27df37\n\n{t}", "source": u, "title": f"{u.rsplit('/', 1)[1]}_9e27df37",
                 "doc_type": "pdf", "chunk_index": i} for t, u, i in rows]
    emb = np.array([[1.0, 0.1 * i, 0.0] if "regolamento" in u else [0.7, 0.0, 0.7] for _, u, i in rows],
                   dtype=np.float32)
    monkeypatch.setattr(vs, "_embeddings", emb / np.linalg.norm(emb, axis=1, keepdims=True))
    monkeypatch.setattr(vs, "_metadata", metadata)
    monkeypatch.setattr(vs, "_ids", [str(i) for i in range(len(rows))])
    monkeypatch.setattr(vs, "_bm25", vs._make_bm25(metadata))
    monkeypatch.setattr(vs, "_bm25_dirty", False)
    monkeypatch.setattr(vs, "_copy_of", None)
    monkeypatch.setattr(vs, "_pdf_title_count", None)


def test_copies_of_a_document_are_listed_as_the_document_itself(copies):
    found = vs.hybrid_search([1.0, 0.05, 0.0], "regolamento edilizio manutenzione", top_k=5)
    sources = [c["source"] for c in found]
    assert set(sources) == {"https://files.example.org/allegati/regolamento.pdf",
                            "https://files.example.org/verde.pdf"}
    assert len(found) == 3   # the two passages of the regulation once each, then the other document


def test_documents_that_only_share_some_passages_are_not_copies():
    meta = [{"source": "https://a.example.org/x.pdf", "text": "uno", "chunk_index": 0},
            {"source": "https://a.example.org/x.pdf", "text": "due", "chunk_index": 1},
            {"source": "https://a.example.org/y.pdf", "text": "uno", "chunk_index": 0}]
    assert vs._find_copies(meta) == {}
