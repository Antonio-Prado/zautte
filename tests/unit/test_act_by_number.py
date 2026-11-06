"""An act asked for by its number («la determina n. 1200 del 2025») is looked up in the index, not
searched: parse_act_query (api/rag.py) and find_acts (indexer/vector_store.py)."""
import numpy as np
import pytest

from api import rag
from indexer import vector_store as vs

PORTAL = "https://example.org/?page_id=36046"


def _card(voceamt, detail, title, number, data, tipo, section="Provvedimenti dirigenti amministrativi dal 2023"):
    text = (f"{title}\n\nComune di Esempio, Amministrazione trasparente, sezione Provvedimenti > {section}: "
            f"{title}.\nNumero: {number}; Data: {data}; Servizio: AREA TEST; Tipo Documento: {tipo}\n"
            f"Pagina del documento: {PORTAL}&voceamt={voceamt}&dettaglio={detail}")
    return {"source": f"{PORTAL}&voceamt={voceamt}&dettaglio={detail}", "title": title, "doc_type": "html",
            "chunk_index": 0, "chunk_total": 1, "origin": "amt", "card": True, "text": text}


def _act(body, number, date, subject):
    title = f"Delibera di {body} comunale n. {number} del {date}: {subject}"
    return {"source": f"https://example.org/?page_id=35564#atto-{date[-4:]}-{number}", "title": title,
            "doc_type": "html", "chunk_index": 0, "chunk_total": 1, "origin": "acts",
            "text": f"{title}\n\nDelibera di {body} comunale n. {number}. Oggetto: {subject}."}


META = [
    _card(50, 1, "RIMBORSO SOMMA ERRONEAMENTE VERSATA", "1200/2025", "03/09/2025", "Determinazione"),
    _card(50, 2, "ANNULLAMENTO ORDINANZA DIRIGENZIALE N. 473 DEL 09.05.2025", "1201/2025", "04/09/2025",
          "Determinazio"),   # a determination about an order: its title must not make it an order
    _card(207, 3, "Occupazione suolo pubblico in via Roma", "1200/2025", "03/09/2025", "Ordinanze",
          section="Provvedimenti dirigenti amministrativi (Ordinanze Polizia Municipale)"),
    _card(49, 4, "SPETTACOLO PIROTECNICO", "162", "15/07/2025", "Delibere"),
    _card(49, 5, "PNRR MISSIONE 5", "162", "31/12/2025", "Delibere"),
    _act("Giunta", 162, "15/07/2025", "SPETTACOLO PIROTECNICO"),
    _act("Consiglio", 162, "31/12/2025", "PNRR MISSIONE 5"),
    {**_card(50, 6, "SECONDO BRANO", "1200/2025", "03/09/2025", "Determinazione"), "chunk_index": 1},
    {"source": "https://example.org/it/page", "title": "Una pagina qualsiasi", "doc_type": "html",
     "chunk_index": 0, "text": "Una pagina qualsiasi\n\nNel 2025 il numero 1200 non è un atto."},
]


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setattr(vs, "_metadata", META)
    monkeypatch.setattr(vs, "_embeddings", np.zeros((len(META), 3), dtype=np.float32))
    monkeypatch.setattr(vs, "_ids", [str(i) for i in range(len(META))])
    monkeypatch.setattr(vs, "_acts_index", None)
    monkeypatch.setattr(vs, "_pdf_title_count", None)
    yield


def test_find_acts_by_kind(store):
    det = vs.find_acts(1200, 2025, "determina")
    assert [c["title"] for c in det] == ["RIMBORSO SOMMA ERRONEAMENTE VERSATA"]
    assert det[0]["score"] == 1.0 and det[0]["source"].endswith("dettaglio=1")
    assert [c["title"] for c in vs.find_acts(1200, 2025, "ordinanza")] == ["Occupazione suolo pubblico in via Roma"]
    assert len(vs.find_acts(1200, 2025)) == 2          # any kind
    assert vs.find_acts(1200, 2024) == []              # the year matters
    assert vs.find_acts(1201, 2025, "ordinanza") == []  # the title says «ordinanza», the type says determination
    assert [c["title"] for c in vs.find_acts(1201, 2025, "determina")] == [
        "ANNULLAMENTO ORDINANZA DIRIGENZIALE N. 473 DEL 09.05.2025"]


def test_find_acts_resolutions_cards_and_register(store):
    found = vs.find_acts(162, 2025, "delibera")
    assert len(found) == 4   # two cards (no body named) and two register entries


@pytest.mark.parametrize("query, expected", [
    ("cosa dice la determina n. 1200 del 2025?", ("determina", 1200, 2025)),
    ("Determinazione dirigenziale numero 1200/2025", ("determina", 1200, 2025)),
    ("mi spieghi la delibera di giunta comunale n. 45 del 2024", ("delibera", 45, 2024)),
    ("deliberazione di consiglio 12-2023", ("delibera", 12, 2023)),
    ("ordinanza n° 1171 del 2026 della polizia municipale", ("ordinanza", 1171, 2026)),
    ("atto n. 7 dell'anno 2025", ("", 7, 2025)),
    ("provvedimento 15 del 2022", ("", 15, 2022)),
])
def test_parse_act_query(query, expected):
    assert rag.parse_act_query(query) == expected


@pytest.mark.parametrize("query", [
    "quanto costa la mensa nel 2025?",
    "la determina 1200",                   # no year
    "delibera n. 3 del 1850",              # not a plausible year
    "regolamento n. 12 del 2020",          # not an act kind
    "ci sono 1200 determine nel 2025",     # number before the kind, no «n.»/«del» pattern
])
def test_parse_act_query_ignores(query):
    assert rag.parse_act_query(query) is None


def test_acts_by_number_keeps_the_body_asked(store):
    found = rag._acts_by_number("cosa dice la delibera di giunta n. 162 del 2025?")
    titles = [c["title"] for c in found]
    assert titles[0].startswith("Delibera di Giunta comunale n. 162 del 15/07/2025")
    assert "SPETTACOLO PIROTECNICO" in titles          # the card with the same date comes too
    assert not any("Consiglio" in t or "PNRR" in t for t in titles)
    assert all(c.get("known") for c in found)
    assert rag._acts_by_number("cosa dice la delibera n. 162 del 2025?") and \
        len(rag._acts_by_number("cosa dice la delibera n. 162 del 2025?")) == 4
    assert rag._acts_by_number("come pago la TARI?") == []
