"""The council sessions job: links grouped under year headings become a calendar with the last
session held and the next one convened; the API picks the dynamic fact up without a restart."""
import json
from datetime import date

import pytest

from scripts import council_sessions as cs

PAGE = """
<h2>Descrizione</h2>
<p><strong>Convocazioni anno 2026</strong></p>
<ul>
<li><a title="x" href="https://files.example.org/odg-2026/ordine-consiglio-12-novembre.docx">O.d.G. Consiglio Comunale del 12/11/2026</a></li>
<li><a href="https://files.example.org/odg-2026/ordine-consiglio-24-ottobre.docx">O.d.G. Consiglio Comunale del 24/10/2026</a></li>
<li><a href="https://files.example.org/odg-2026/ordine-del-giorno-question-time.doc">O.d.G. Consiglio Comunale del 16/07/2026</a></li>
<li><a href="/relative/primo-consiglio.doc">O.d.G. <strong>Consiglio</strong> Comunale del 13/06/2026</a></li>
</ul>
<p><strong>Convocazioni anno 2025</strong></p>
<ul>
<li><a href="https://files.example.org/odg-2025/novembre.doc">O.d.G. Consiglio Comunale del&nbsp;19 novembre</a></li>
<li><a href="https://files.example.org/odg-2025/lettera.doc">O.d.G. Consiglio comunale del 24 maggio in seduta aperta</a></li>
<li><a href="https://files.example.org/odg-2025/giugno.doc">O.d.G. Consiglio comunale del 26 giugno</a></li>
<li><a href="https://files.example.org/altro.pdf">Regolamento del Consiglio comunale</a></li>
<li><a href="https://files.example.org/odg-2025/giugno.doc">O.d.G. Consiglio comunale del 26 giugno</a></li>
</ul>
"""
URL = "https://www.example.org/it/public_documents/convocazioni-consiglio-comunale"


def test_parse_dates_and_years():
    sessions = cs.parse_sessions(PAGE, URL)
    assert [s["date"].isoformat() for s in sessions] == [
        "2025-05-24", "2025-06-26", "2025-11-19", "2026-06-13", "2026-07-16", "2026-10-24", "2026-11-12"]
    by_date = {s["date"]: s for s in sessions}
    assert by_date[date(2026, 7, 16)]["note"] == "question time"
    assert by_date[date(2025, 5, 24)]["note"] == "seduta aperta"
    assert by_date[date(2026, 6, 13)]["url"] == "https://www.example.org/relative/primo-consiglio.doc"


def test_fact_last_and_next():
    sessions = cs.parse_sessions(PAGE, URL)
    fact = cs.build_fact(sessions, date(2026, 10, 25), URL, "Comune di Esempio")
    text = fact["text"]
    assert "Ultima seduta svolta: sabato 24 ottobre 2026" in text
    assert "Prossima seduta convocata: giovedì 12 novembre 2026" in text
    assert "Sedute del 2026 (anno in corso), in ordine cronologico: 13 giugno, 16 luglio (question time), 24 ottobre, 12 novembre." in text
    assert "ATTENZIONE: le date elencate sotto «Sedute del 2025» sono dell'anno 2025" in text
    assert fact["id"] == cs.FACT_ID and "consiglio" in fact["require_any"]


def test_fact_without_a_next_session():
    sessions = [s for s in cs.parse_sessions(PAGE, URL) if s["date"] < date(2026, 11, 1)]
    text = cs.build_fact(sessions, date(2026, 10, 25), URL, "Comune di Esempio")["text"]
    assert "non è ancora pubblicata la convocazione di una seduta successiva" in text
    # the session of the day counts as held
    text = cs.build_fact(sessions, date(2026, 10, 24), URL, "Comune di Esempio")["text"]
    assert "Ultima seduta svolta: oggi, sabato 24 ottobre 2026" in text


def test_upsert_keeps_other_facts_and_is_idempotent(tmp_path):
    f = tmp_path / "facts" / "dyn.json"
    f.parent.mkdir()
    f.write_text(json.dumps([{"id": "other", "keywords": ["x"], "title": "t", "text": "x"}]), encoding="utf-8")
    fact = cs.build_fact(cs.parse_sessions(PAGE, URL), date(2026, 10, 25), URL, "Comune di Esempio")
    assert cs.upsert_fact(f, fact) is True
    assert cs.upsert_fact(f, fact) is False
    ids = [e["id"] for e in json.loads(f.read_text(encoding="utf-8"))]
    assert ids == ["other", cs.FACT_ID]


def test_no_year_no_session():
    assert cs.parse_sessions('<a href="/x.doc">Consiglio comunale del 26 giugno</a>', URL) == []


@pytest.fixture
def dynamic_file(tmp_path, monkeypatch):
    from api import rag
    f = tmp_path / "known_facts_dynamic.json"
    monkeypatch.setattr(rag, "KNOWN_FACTS_DYNAMIC_FILE", f)
    # -inf, not 0.0: on a machine up for less than a minute monotonic() is below the check interval
    monkeypatch.setitem(rag._DYNAMIC_FACTS, "checked", float("-inf"))
    monkeypatch.setitem(rag._DYNAMIC_FACTS, "mtime", None)
    monkeypatch.setitem(rag._DYNAMIC_FACTS, "facts", [])
    return f


def test_api_reads_the_dynamic_file_when_it_changes(dynamic_file):
    import os

    from api import rag
    assert not any(c["title"] == "Dyn" for c in rag._inject_known_facts("quando è il prossimo consiglio?"))
    dynamic_file.write_text(json.dumps([{"id": "d", "keywords": ["consiglio"], "title": "Dyn",
                                         "text": "calendario", "source": "https://www.example.org/c"}]),
                            encoding="utf-8")
    rag._DYNAMIC_FACTS["checked"] = float("-inf")
    hits = rag._inject_known_facts("quando è il prossimo consiglio?")
    assert any(c["title"] == "Dyn" and c["score"] == 1.0 for c in hits)
    assert not any(c["title"] == "Dyn" for c in rag._inject_known_facts("come pago la TARI?"))
    # removed file → fact gone, after the check interval
    os.unlink(dynamic_file)
    rag._DYNAMIC_FACTS["checked"] = float("-inf")
    assert not any(c["title"] == "Dyn" for c in rag._inject_known_facts("quando è il prossimo consiglio?"))
