"""Grants looked up by the beneficiary's name (vector_store.find_grants, rag._grants_by_beneficiary) and the
latest yearly version brought in when the search found only an older one (vector_store.latest_version_passages)."""
import numpy as np
import pytest

from api import rag
from indexer import vector_store as vs

PORTAL = "https://example.org/?page_id=36046"


def _card(detail, title, beneficiary, amount, date, vec=(1.0, 0.0, 0.0)):
    text = (f"{title}\n\nComune di Esempio, Amministrazione trasparente, sezione Sovvenzioni, contributi, sussidi, "
            f"vantaggi economici > Atti di concessione: {title}.\nBeneficiario: {beneficiary}; Importo: {amount}; "
            f"Norma / Tipo: Determina; Numero atto: 1; Data atto: {date}; Ufficio competente: AREA; "
            f"Responsabile: X; Tipo Documento: Contributi e sovvenzioni\nPubblicato dal {date}.\n"
            f"Pagina del documento: {PORTAL}&voceamt=197&dettaglio={detail}")
    return ({"source": f"{PORTAL}&voceamt=197&dettaglio={detail}", "title": title, "doc_type": "html",
             "chunk_index": 0, "origin": "amt", "card": True, "text": text}, vec)


def _doc(voceamt, slug, title, i, text, vec=(1.0, 0.0, 0.0)):
    return ({"source": f"{PORTAL}&voceamt={voceamt}#{slug}", "title": title, "doc_type": "pdf",
             "chunk_index": i, "origin": "amt", "text": f"{title}\n\n{text}"}, vec)


ROWS = [
    _card(1, "SPIAGGIA LIBERA TUTTI 2025", "CROCE ROSSA ITALIANA COMITATO LOCALE S. BENEDETTO DEL TRONTO A.P.S.",
          "10.000,00 €", "19/01/2026"),
    _card(2, "FIERA 1 MAGGIO", "CROCE ROSSA ITALIANA COMITATO LOCALE S. BENEDETTO DEL TRONTO A.P.S.", "866,00 €",
          "29/04/2025"),
    _card(3, "CUSTODIA ATTREZZATURE 2021", "CROCE ROSSA ITALIANA - COMITATO DI SAN BENEDETTO", "", "10/03/2022"),
    _card(4, "RIMBORSO", "AVIS COMUNALE", "500,00 €", "01/02/2025"),
    _card(5, "GESTIONE", "COOPERATIVA SOCIALE ATTIVAMENTE ASSISTENZA", "1.000,00 €", "01/06/2025"),
    _card(6, "PRIVATO", "B. 171741", "300,00 €", "01/06/2025"),
    _doc(171, "piao-2024-2026", "PIANO INTEGRATO ATTIVITA' E ORGANIZZAZIONE (PIAO) 2024/2026 APPROVATO CON "
         "DELIBERAZIONE DI GIUNTA COMUNALE N°53 DEL 15/04/2024", 0, "piano vecchio", vec=(0.9, 0.1, 0.0)),
    _doc(171, "piao-2026-2028", "PIANO INTEGRATO ATTIVITA' E ORGANIZZAZIONE (PIAO) 2026/2028 approvato con "
         "Deliberazione di Giunta comunale n. 19 del 23.02.2026", 0, "intestazione", vec=(0.0, 0.0, 1.0)),
    _doc(171, "piao-2026-2028", "PIANO INTEGRATO ATTIVITA' E ORGANIZZAZIONE (PIAO) 2026/2028 approvato con "
         "Deliberazione di Giunta comunale n. 19 del 23.02.2026", 1, "misure anticorruzione nuove", vec=(0.8, 0.2, 0.0)),
    _card(7, "REGATA", "A.S.D. STELLA DEL MARE", "400,00 €", "01/07/2025"),
]
META = [m for m, _ in ROWS]
VECS = np.array([v for _, v in ROWS], dtype=np.float32)


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setattr(vs, "_metadata", META)
    monkeypatch.setattr(vs, "_embeddings", VECS / np.linalg.norm(VECS, axis=1, keepdims=True))
    monkeypatch.setattr(vs, "_ids", [str(i) for i in range(len(META))])
    for name in ("_versions", "_grants", "_source_rows", "_pdf_title_count"):
        monkeypatch.setattr(vs, name, None)
    yield


def test_grant_tokens_keep_only_the_distinctive_words():
    assert vs._grant_tokens("CROCE ROSSA ITALIANA COMITATO LOCALE S. BENEDETTO DEL TRONTO A.P.S.") == {"croce", "rossa"}
    assert vs._grant_tokens("AVIS COMUNALE") == {"avis"}
    assert vs._grant_tokens("B. 171741") == frozenset()
    assert vs._grant_tokens("COMUNE DI SAN BENEDETTO DEL TRONTO") == frozenset()
    assert vs._grant_tokens("Società Cooperativa Sociale «L'Àncora»") == {"ancora"}


def test_find_grants_by_name(store):
    cards, info = vs.find_grants({"quali", "contributi", "croce", "rossa", "comune"})
    assert [c["source"][-1] for c in cards] == ["1", "2", "3"]       # most recent first, both spellings
    assert info["count"] == 3 and info["years"] == ("2022", "2026") and info["total"] == 10866.0
    assert info["with_amount"] == 2 and info["year"] is None
    assert min(info["beneficiaries"]).startswith("CROCE ROSSA ITALIANA - COMITATO")
    cards, info = vs.find_grants({"contributi", "croce", "rossa"}, year=2025)
    assert [c["source"][-1] for c in cards] == ["2"] and info["year"] == 2025 and info["total"] == 866.0
    cards, info = vs.find_grants({"contributi", "croce", "rossa"}, year=2019)   # no card of that year: all
    assert len(cards) == 3 and info["year"] is None
    assert vs.find_grants({"contributi", "avis"})[1]["count"] == 1
    assert vs.find_grants({"quanto", "dato", "cooperativa", "attivamente"})[1]["count"] == 1   # a rare word is enough
    assert vs.find_grants({"contributi", "rossa"})[1]["count"] == 3     # «rossa»: two beneficiaries have it, rare
    assert vs.find_grants({"contributi", "comitato"}) == ([], {})        # a generic word is not a name word
    # «se fossi uscito in mare… dato che»: a short everyday word that happens to be in few names is not a name
    assert vs.find_grants({"dato", "uscito", "mare", "peschereccio"}) == ([], {})
    assert vs.find_grants({"contributi", "stella", "mare"})[1]["count"] == 1
    assert vs.find_grants({"contributi", "associazione", "sportiva"}) == ([], {})
    assert vs.find_grants({"contributi", "comune", "benedetto"}) == ([], {})


def test_grants_by_beneficiary_summary(store):
    found = rag._grants_by_beneficiary("quali contributi ha ricevuto la Croce Rossa dal Comune nel 2025?")
    assert len(found) == 2 and not found[0].get("known") and not found[1].get("known")   # listed only when cited
    assert "a favore di CROCE ROSSA" in found[0]["text"] and "1 schede nell'anno 2025" in found[0]["text"]
    assert "866,00 €" in found[0]["text"] and found[0]["source"].endswith("voceamt=197")
    assert found[1]["source"].endswith("dettaglio=2")
    assert rag._grants_by_beneficiary("dove ha sede la Croce Rossa?") == []    # no money asked
    assert len(rag._grants_by_beneficiary("quanto ha dato il Comune alla cooperativa Attivamente?")) == 2
    assert rag._grants_by_beneficiary("quali contributi per le famiglie?") == []


def test_latest_version_passages(store):
    old = META[6]
    chosen = [{"text": old["text"], "source": old["source"], "title": old["title"], "score": 0.6}]
    assert vs.version_info(old["source"]) == (2026, 2028)      # the last year of «2024/2026» and of «2026/2028»: same series despite «N°53 DEL» vs «n. 19 del»
    found = vs.latest_version_passages(chosen, [0.8, 0.2, 0.0])
    assert len(found) == 1 and found[0]["source"].endswith("piao-2026-2028")
    assert "misure anticorruzione nuove" in found[0]["text"]
    # the latest version is already among the passages: nothing added
    chosen.append({"text": META[7]["text"], "source": META[7]["source"], "title": META[7]["title"], "score": 0.5})
    assert vs.latest_version_passages(chosen, [0.8, 0.2, 0.0]) == []
