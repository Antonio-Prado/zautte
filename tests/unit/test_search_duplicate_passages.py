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


def test_copies_of_a_passage_do_not_take_the_place_of_other_pages(store):
    found = vs.hybrid_search([1.0, 0.0, 0.0], "contributi familiari strutture residenziali", top_k=3)
    assert [c["source"].rsplit("/", 1)[1] for c in found][1:] == ["donne", "rette"]
    assert found[0]["source"].split("?")[0].endswith("/it/news/contributi")
