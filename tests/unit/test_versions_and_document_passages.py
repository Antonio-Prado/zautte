"""Yearly versions of a transparency document (vector_store.version_info, the penalty in rag.rerank)
and the passage of a chosen document closest to the question (vector_store.document_passages)."""
import numpy as np
import pytest

from api import rag
from indexer import vector_store as vs

PORTAL = "https://example.org/?page_id=36046"


def _doc(voceamt, slug, title, i, text, origin="amt", vec=(1.0, 0.0, 0.0)):
    return ({"source": f"{PORTAL}&voceamt={voceamt}#{slug}", "title": title, "doc_type": "pdf",
             "chunk_index": i, "origin": origin, "text": f"{title}\n\n{text}"}, vec)


ROWS = [
    _doc(173, "compensi-2022", "Compensi degli Amministratori comunali anno 2022", 0, "Allegati: 2022.ods"),
    _doc(173, "compensi-2024", "Compensi degli Amministratori comunali anno 2024", 0, "Allegati: 2024.ods"),
    _doc(173, "compensi-2025", "Compensi degli Amministratori comunali anno 2025", 0,
         "Allegati: Organi di indirizzo politico 2025.ods", vec=(1.0, 0.0, 0.0)),
    _doc(173, "compensi-2025", "Compensi degli Amministratori comunali anno 2025", 1,
         "[Organi.ods] Spazzafumo Antonio Sindaco € 51.272,31", vec=(0.0, 1.0, 0.0)),
    _doc(173, "compensi-2025", "Compensi degli Amministratori comunali anno 2025", 2,
         "[Organi.ods] Consiglieri: gettoni", vec=(0.6, 0.8, 0.0)),
    _doc(171, "ptpct-2021-2023", "PIANO PREVENZIONE CORRUZIONE E TRASPARENZA 2021/2023", 0, "piano"),
    _doc(171, "ptpct-2022-2024", "PIANO PREVENZIONE CORRUZIONE E TRASPARENZA 2022/2024", 0, "piano"),
    _doc(535, "2024", "2024", 0, "Allegati: indicatore 2024.pdf"),
    _doc(535, "2025", "2025", 0, "Allegati: indicatore 2025.pdf"),
    _doc(24, "cv", "Curriculum vitae", 0, "un curriculum"),           # no year: not a series
    _doc(24, "organico-2025", "Dotazione organica al 31 dicembre 2025", 0, "321 dipendenti"),   # alone
    _doc(9, "news-2022", "Anno 2022", 0, "notizia", origin="news"),  # not a transparency document
    _doc(9, "news-2023", "Anno 2023", 0, "notizia", origin="news"),
]
SITE = "https://www.example.org/it"


def _page(path, title, origin=None):
    return ({"source": f"{SITE}/{path}", "title": title, "doc_type": "html", "chunk_index": 0,
             "origin": origin, "text": f"{title}\n\ntesto"}, (1.0, 0.0, 0.0))


ROWS += [
    _page("page/censimento-2023", "2023, censimento permanente della popolazione (annuale) - Comune"),
    _page("page/censimento-2026", "2026, censimento permanente della popolazione (annuale) - Comune"),
    _page("news/1", "Censimento permanente della popolazione 2024 - Comune"),        # news: never a version
    _page("news/2", "Censimento permanente della popolazione 2026 - Comune"),
    _page("page/comitato-1", "Comitato dei sindaci n. 1 del 12/03/2026 - Ambito"),   # same year: different pages
    _page("page/comitato-2", "Comitato dei sindaci n. 2 del 20/05/2026 - Ambito"),
]
META = [m for m, _ in ROWS]
VECS = np.array([v for _, v in ROWS], dtype=np.float32)


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setattr(vs, "_metadata", META)
    monkeypatch.setattr(vs, "_embeddings", VECS / np.linalg.norm(VECS, axis=1, keepdims=True))
    monkeypatch.setattr(vs, "_ids", [str(i) for i in range(len(META))])
    monkeypatch.setattr(vs, "_versions", None)
    monkeypatch.setattr(vs, "_source_rows", None)
    monkeypatch.setattr(vs, "_pdf_title_count", None)
    yield


def test_version_info(store):
    assert vs.version_info(META[0]["source"]) == (2022, 2025)
    assert vs.version_info(META[1]["source"]) == (2024, 2025)
    assert vs.version_info(META[2]["source"]) == (2025, 2025)
    assert vs.version_info(META[5]["source"]) == (2023, 2024)     # «2021/2023»: the last year counts
    assert vs.version_info(META[6]["source"]) == (2024, 2024)
    assert vs.version_info(META[7]["source"]) == (2024, 2025)     # titles that are just the year
    assert vs.version_info(META[9]["source"]) is None             # no year in the title
    assert vs.version_info(META[10]["source"]) is None            # a single version
    assert vs.version_info(META[11]["source"]) is None            # news, not transparency
    assert vs.version_info("https://example.org/nowhere") is None


def test_version_info_of_web_pages(store):
    assert vs.version_info(f"{SITE}/page/censimento-2023") == (2023, 2026)
    assert vs.version_info(f"{SITE}/page/censimento-2026") == (2026, 2026)
    assert vs.version_info(f"{SITE}/news/1") is None
    assert vs.version_info(f"{SITE}/page/comitato-1") is None


def _chunk(meta, score=0.60):
    return {"text": meta["text"], "source": meta["source"], "title": meta["title"], "doc_type": "html",
            "category": "documento", "score": score}


def test_rerank_prefers_the_latest_version(store):
    chunks = [_chunk(META[0]), _chunk(META[1]), _chunk(META[2])]
    order = [c["title"][-4:] for c in rag.rerank(chunks, "compensi degli amministratori comunali")]
    assert order == ["2025", "2024", "2022"]
    # the question names the year of the older one: no penalty for it
    order = [c["title"][-4:] for c in rag.rerank(chunks, "compensi degli amministratori comunali nel 2022")]
    assert order[0] == "2022"


def test_document_passages_closest_to_the_question(store):
    chosen = [_chunk(META[2]), _chunk(META[10])]           # the heading of 2025 and a one-passage document
    found = vs.document_passages(chosen, [0.0, 1.0, 0.0])
    assert len(found) == 1
    assert "Spazzafumo Antonio Sindaco" in found[0]["text"]
    assert found[0]["source"] == META[2]["source"] and found[0]["score"] == pytest.approx(1.0)
    # the chosen passage is not the heading: nothing added
    assert vs.document_passages([_chunk(META[3])], [0.0, 1.0, 0.0]) == []
    assert vs.document_passages(chosen, [0.0, 1.0, 0.0], limit=0) == []
