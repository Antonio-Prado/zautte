"""Passages of the PDFs attached to the pages the search picked (vector_store.attachment_passages)."""
import json

import numpy as np
import pytest

from indexer import vector_store as vs

PAGE = "https://example.org/it/news/regole-spiaggia"
PDF = "https://files.example.org/regolamento-litorale.pdf"
OTHER = "https://files.example.org/altro.pdf"


def _meta(source, i, text, doc_type="pdf"):
    return {"source": source, "chunk_index": i, "title": "Regolamento", "doc_type": doc_type,
            "text": f"Regolamento\n\n{text}"}


@pytest.fixture
def store(monkeypatch):
    vecs = np.array([
        [1.0, 0.0, 0.0],      # page
        [0.0, 1.0, 0.0],      # pdf chunk 0
        [0.6, 0.8, 0.0],      # pdf chunk 1: the closest to the query
        [0.0, 0.0, 1.0],      # pdf chunk 2: follows chunk 1
        [0.0, 0.6, 0.8],      # other pdf
    ], dtype=np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    meta = [_meta(PAGE, 0, "Vita in spiaggia", "html"),
            _meta(PDF, 0, "Articolo 1"), _meta(PDF, 1, "file distanziate non meno di mt."),
            _meta(PDF, 2, "1,15 dai confini della concessione"), _meta(OTHER, 0, "Altro")]
    monkeypatch.setattr(vs, "_embeddings", vecs)
    monkeypatch.setattr(vs, "_metadata", meta)
    monkeypatch.setattr(vs, "_ids", [str(i) for i in range(len(meta))])
    monkeypatch.setattr(vs, "_ensure_loaded", lambda: None)
    monkeypatch.setattr(vs, "_source_rows", None)
    monkeypatch.setattr(vs, "_copy_of", {})
    monkeypatch.setattr(vs, "_page_attachments", {PAGE: [PDF, OTHER]})
    return meta


def test_best_passage_of_an_attachment_joined_to_the_next(store):
    (hit,) = vs.attachment_passages([PAGE], [0.5, 0.85, 0.0], limit=1)
    assert hit["source"] == PDF
    assert "non meno di mt." in hit["text"] and "1,15 dai confini" in hit["text"]   # the cut sentence, whole
    assert hit["text"].count("Regolamento") == 1                                    # the title line once


def test_limits_threshold_and_sources_already_in_the_context(store):
    assert len(vs.attachment_passages([PAGE], [0.5, 0.85, 0.0], limit=2)) == 2
    assert vs.attachment_passages([PAGE], [0.5, 0.85, 0.0], limit=2, min_score=0.999) == []
    assert [h["source"] for h in vs.attachment_passages([PAGE], [0.5, 0.85, 0.0], limit=2, skip_sources={PDF})] == [OTHER]
    assert vs.attachment_passages(["https://example.org/other-page"], [0.5, 0.85, 0.0]) == []
    assert vs.attachment_passages([PAGE], [0.0, 0.0, 0.0]) == []


def test_pages_with_too_many_attachments_are_left_out(tmp_path, monkeypatch):
    path = tmp_path / "page_attachments.json"
    path.write_text(json.dumps({PAGE: [PDF], "https://example.org/modulistica": [f"{PDF}{i}" for i in range(30)]}))
    monkeypatch.setattr(vs, "CRAWL_CACHE_DIR", tmp_path)
    monkeypatch.setattr(vs, "_page_attachments", None)
    assert vs._attachments_of_pages() == {PAGE: [PDF]}


def test_crawler_keeps_the_pages_it_did_not_read(tmp_path, monkeypatch):
    from crawler import crawler
    monkeypatch.setattr(crawler, "CRAWL_CACHE_DIR", tmp_path)
    (tmp_path / "page_attachments.json").write_text(json.dumps({"a": ["x.pdf"], "b": ["y.pdf"]}))
    assert crawler.save_page_attachments({"a": [], "c": ["z.pdf", "z.pdf"]}) == 2
    assert json.loads((tmp_path / "page_attachments.json").read_text()) == {"b": ["y.pdf"], "c": ["z.pdf"]}
