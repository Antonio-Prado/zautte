"""The tenders job: the tender documents of the transparency portal become one known fact with the
procedures still open today, the recent ones without a deadline and the ones just closed."""
from datetime import date

from scripts import tender_notices as tn

PORTAL = "https://example.nuvolapalitalsoft.it/"
SECTION = PORTAL + "?page_id=36046&voceamt=561"
TODAY = date(2026, 11, 13)


def block(title, description):
    return (f'<div class="italsoft-alert"><h2>{title}</h2><p>{description}</p><ul><li>'
            '<a href="?page_id=36046&amp;event=vediAllegato&amp;id=T1">Bando.pdf</a> MD5: 0123456789abcdef0123456789abcdef'
            '</li></ul></div>')


PAGE = ('<html><body><div class="page-content"><h1>Documenti di gara</h1>'
        + block("SERVIZIO MUSEO", "Bando per servizio da realizzarsi sulla piattaforma https://appalti.example.it/Portale/ "
                "Scadenza presentazione offerte: 03/12/2026 ore 11:00 LINK PAD")
        + block("SERVIZI ASSICURATIVI", "PER IL PERIODO DALLE ORE 24:00 DEL 31/12/2026 ALLE ORE 24:00 DEL 31/12/2029 "
                "Scadenza presentazione offerte: 13/11/2026 ore 18:00")
        + block("LUMINARIE", "Scadenza 02/11/2026 ore 12 :00 Link PAD")
        + block("VECCHIA GARA", "SCADENZA 03/07/2026 ORE 12")
        + block("SERVIZIO MUSEO", "Avviso senza data")
        + "</div></body></html>")


def test_the_deadline_is_the_one_after_the_word_scadenza():
    assert tn.deadline("DALLE ORE 24:00 DEL 31/12/2026 … Scadenza presentazione offerte: 04/12/2026 ore 18:00") == (
        date(2026, 12, 4), "ore 18:00")
    assert tn.deadline("Scadenza 03/11/2026 ore 11 :00 Link") == (date(2026, 11, 3), "ore 11:00")
    assert tn.deadline("SCADENZA 03/07/2026 ORE 12") == (date(2026, 7, 3), "ore 12:00")
    assert tn.deadline("Scadenza presentazione offerte 26/08/2026 LINK PAD") == (date(2026, 8, 26), "")
    assert tn.deadline("Avviso di indagine di mercato") is None and tn.deadline("Scadenza 31/02/2026") is None


def tenders():
    return tn.read_tenders(lambda url: PAGE, PORTAL, 36046, [561])


def test_open_today_closed_recently_and_dropped():
    found = tenders()
    assert found[0]["source"] == SECTION + "#servizio-museo" and found[4]["source"] == SECTION + "#servizio-museo-2"
    assert found[0]["links"] == ["https://appalti.example.it/Portale/"]
    by = tn.classify(found, TODAY)
    assert [t["title"] for t in by["open"]] == ["SERVIZI ASSICURATIVI", "SERVIZIO MUSEO"]   # the deadline day is open
    assert [t["title"] for t in by["closed"]] == ["LUMINARIE"]                             # July: too old to list
    assert by["undated"] == []                                                             # no publication date: not listed


def test_the_fact_says_what_is_open_and_what_is_not():
    fact = tn.build_fact(tn.classify(tenders(), TODAY), TODAY, SECTION, "Comune di Esempio")
    text = fact["text"]
    open_part = text.split("PROCEDURE SCADUTE")[0]
    assert "SERVIZIO MUSEO (scadenza: 03/12/2026 ore 11:00)" in open_part and "LUMINARIE" not in open_part
    assert "Piattaforma: https://appalti.example.it/Portale/" in open_part and "VECCHIA GARA" not in text
    assert "non si possono più presentare offerte" in text and fact["id"] == "tenders-open" and fact["source"] == SECTION
    none = tn.build_fact(tn.classify(tenders(), date(2027, 3, 1)), date(2027, 3, 1), SECTION, "Comune di Esempio")["text"]
    assert "Nessuna: al 01/03/2027" in none


def test_the_api_injects_it_for_tenders_only(tmp_path, monkeypatch):
    from api import rag
    from scripts.council_sessions import upsert_fact
    path = tmp_path / "facts" / "known_facts_dynamic.json"
    fact = tn.build_fact(tn.classify(tenders(), TODAY), TODAY, SECTION, "Comune di Esempio")
    assert upsert_fact(path, fact) is True
    monkeypatch.setattr(rag, "KNOWN_FACTS_DYNAMIC_FILE", path)
    monkeypatch.setattr(rag, "_DYNAMIC_FACTS", {"facts": [], "mtime": None, "checked": float("-inf")})
    titles = lambda q: [c["title"] for c in rag._inject_known_facts(q)]
    assert fact["title"] in titles("Ci sono gare d'appalto aperte adesso?")
    assert fact["title"] not in titles("Ci sono concorsi aperti?")
