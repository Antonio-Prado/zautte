"""The office heads job: the pages of the site that list the offices and the page of each one become
one known fact with the head each page names today."""
import json
from datetime import date

import httpx
import pytest

from scripts import office_heads as oh

SITE = "https://www.example.org"
INDEX = SITE + "/it/page/uffici"
AREAS = SITE + "/it/page/aree-amministrative"
TODAY = date(2026, 11, 26)


def card(name, slug):
    url = f"{SITE}/it/unita_organizzative/{slug}"
    return (f'<div class="card"><a href="{url}" class="custom-link-reference"><h3>{name}</h3></a>'
            f'<a class="read-more" href="{url}">Vai alla Pagina</a></div>')


def section(anchor, title, body):
    return (f'<div class="page-content paragraph"><a id="{anchor}" class="anchor-content" href="#">Municipium</a>'
            f"<h2>{title} </h2>{body}</div>")


def person(name):
    return (f'<div class="card"><a href="{SITE}/it/persone/x" class="custom-link-reference"><h3 class="card-title">'
            f'\n   {name}\n </h3></a><a class="read-more" href="{SITE}/it/persone/x">Vai alla Pagina</a></div>')


def unit_page(head=None, kind="ufficio", parent=None, updated="17 ottobre 2026, 09:09", people=()):
    body = "<h1>|</h1><h1>Ufficio</h1>" + section("competenze", "Competenze", "<ul><li>Tasse</li></ul>")
    if parent:
        body += section("unit-organizzativa-genitore", "Unità organizzativa genitore", person(parent))
    if head is not None:
        body += section("responsabile", "Responsabile", "".join(person(h) for h in head))
    if kind:
        body += section("tipo-di-organizzazione", "Tipo di organizzazione", f"<div>{kind}</div>")
    body += section("persone", "Persone che compongono la struttura", "".join(person(p) for p in people))
    body += f"<p><b>Ultimo aggiornamento</b>: {updated}</p><h2>Quanto sono chiare le informazioni?</h2><h3>Altro</h3>"
    return f"<html><body>{body}</body></html>"


PAGES = {
    INDEX: ("<html><body><nav><a href='/it/page/aree-amministrative'>Aree</a></nav>"
            + card("Tributi e recupero coattivo", "tributi-e-recupero-coattivo") + card("Mobilità", "mobilita")
            + '<a href="?page=1">1</a><a href="?page=2">2</a><a href="?page=2">Successiva »</a></body></html>'),
    INDEX + "?page=2": ("<html><body>" + card("Patrimonio", "patrimonio") + card("Ufficio chiuso", "chiuso")
                        + '<a href="?page=1">1</a><a href="?page=2">2</a></body></html>'),
    AREAS: "<html><body>" + card("Risorse", "risorse") + card("Mobilità", "mobilita") + "</body></html>",
    SITE + "/it/unita_organizzative/tributi-e-recupero-coattivo": unit_page(
        ["Giulia Verdi"], people=["Giulia Verdi", "Mario Gialli"]),
    SITE + "/it/unita_organizzative/mobilita": unit_page(["Paolo Neri"], parent="Polizia municipale",
                                                         updated="1 giugno 2026, 15:51"),
    SITE + "/it/unita_organizzative/patrimonio": unit_page(None, people=["Anna Bianchi"]),
    SITE + "/it/unita_organizzative/risorse": unit_page([], kind="area", updated=""),
}


def get(url):
    if url not in PAGES:
        request = httpx.Request("GET", url)
        raise httpx.HTTPStatusError("gone", request=request, response=httpx.Response(404, request=request))
    return PAGES[url]


def units():
    return oh.read_units(get, [INDEX, AREAS])


def test_the_list_pages_give_the_units_once_with_their_name_and_the_other_pages():
    found, more = oh.parse_index(PAGES[INDEX], INDEX)
    assert found == [("Tributi e recupero coattivo", SITE + "/it/unita_organizzative/tributi-e-recupero-coattivo"),
                     ("Mobilità", SITE + "/it/unita_organizzative/mobilita")]
    assert more == [INDEX + "?page=2"]


def test_the_head_is_the_name_under_its_heading_not_the_people_of_the_office():
    page = oh.parse_unit(PAGES[SITE + "/it/unita_organizzative/tributi-e-recupero-coattivo"])
    assert page == {"heads": ["Giulia Verdi"], "kind": "ufficio", "parent": "", "updated": "17 ottobre 2026"}
    assert oh.parse_unit(unit_page(None, people=["Anna Bianchi"]))["heads"] == []     # no heading: no head
    assert oh.parse_unit(unit_page([], kind="area"))["heads"] == []                   # heading without a name
    assert oh.parse_unit(unit_page(["A B", "C D"]))["heads"] == ["A B", "C D"]
    assert oh.parse_unit(unit_page(["A B"], parent="Polizia municipale"))["parent"] == "Polizia municipale"


def test_a_page_that_is_not_the_page_of_a_unit_is_an_error_not_an_office_without_a_head():
    with pytest.raises(ValueError):
        oh.parse_unit("<html><title>429</title>429 Too Many Requests</html>")
    pages = dict(PAGES)
    pages[SITE + "/it/unita_organizzative/patrimonio"] = "<html><body><h2>Accedi</h2></body></html>"
    with pytest.raises(ValueError, match="patrimonio"):
        oh.read_units(lambda url: pages[url] if url in pages else get(url), [INDEX, AREAS])


def test_every_listed_unit_is_read_once_and_one_that_is_gone_is_skipped():
    found = units()
    assert [u["name"] for u in found] == ["Tributi e recupero coattivo", "Mobilità", "Patrimonio", "Risorse"]
    assert found[1] == {"name": "Mobilità", "url": SITE + "/it/unita_organizzative/mobilita",
                        "heads": ["Paolo Neri"], "kind": "ufficio", "parent": "Polizia municipale",
                        "updated": "1 giugno 2026"}

    def failing(url):
        if url.endswith("/patrimonio"):
            request = httpx.Request("GET", url)
            raise httpx.HTTPStatusError("busy", request=request, response=httpx.Response(429, request=request))
        return get(url)
    with pytest.raises(httpx.HTTPStatusError):     # any other error: nothing is written in part
        oh.read_units(failing, [INDEX, AREAS])


def test_the_fact_names_the_head_of_each_office_and_says_when_the_page_names_none():
    fact = oh.build_fact(units(), TODAY, [INDEX, AREAS], "Comune di Esempio")
    text = fact["text"]
    assert ("- Tributi e recupero coattivo (ufficio): responsabile Giulia Verdi. Pagina (ultimo aggiornamento: "
            f"17 ottobre 2026): {SITE}/it/unita_organizzative/tributi-e-recupero-coattivo") in text
    assert "- Mobilità (ufficio; fa parte di: Polizia municipale): responsabile Paolo Neri." in text
    assert f"- Patrimonio (ufficio): la pagina non indica un responsabile. Pagina (ultimo aggiornamento: 17 ottobre 2026): {SITE}" in text
    assert f"- Risorse (area): la pagina non indica un responsabile. Pagina: {SITE}/it/unita_organizzative/risorse" in text
    assert "Mario Gialli" not in text and "Anna Bianchi" not in text and "Ufficio chiuso" not in text
    assert "Non citare uffici" not in text      # the fact rules on who heads the listed offices, on nothing else
    assert "letto automaticamente il 26 novembre 2026" in text and fact["id"] == "office-heads" and fact["source"] == INDEX
    assert {"tributi", "mobilità", "mobilita", "verdi", "ufficio"} <= set(fact["require_any"])
    assert "responsabile" in fact["keywords"] and "responsabile" not in fact["require_any"]


def test_the_keywords_of_the_office_map_join_the_gate_for_the_listed_units_only(tmp_path):
    path = tmp_path / "offices.json"
    path.write_text(json.dumps([
        {"keywords": ["tari", "imu", "tassa rifiuti"], "name": "Tributi", "path": "/it/unita_organizzative/tributi-e-recupero-coattivo"},
        {"keywords": ["anagrafe"], "name": "Anagrafe", "path": "/it/unita_organizzative/non-in-elenco"},
        {"keywords": ["ztl"], "name": "Mobilità", "url": SITE + "/it/unita_organizzative/mobilita"}]), encoding="utf-8")
    assert oh.office_keywords(units(), path, SITE) == {"tari", "imu", "ztl"}
    assert oh.office_keywords(units(), tmp_path / "missing.json", SITE) == set()
    fact = oh.build_fact(units(), TODAY, [INDEX], "Comune di Esempio", extra_words={"tari"})
    assert "tari" in fact["require_any"]
    assert oh.build_fact(units(), TODAY, [INDEX], "Comune di Esempio", require_any=["x"], extra_words={"tari"})["require_any"] == ["x"]


def test_a_busy_site_is_asked_again_after_a_wait_and_the_requests_are_paced(monkeypatch):
    answers = [httpx.Response(429, headers={"retry-after": "7"}), httpx.Response(503), httpx.Response(200, text="ok"),
               httpx.Response(200, text="second"), httpx.Response(500)]
    asked, slept = [], []

    def fake(client, url, domains):
        asked.append(url)
        r = answers.pop(0)
        r.request = httpx.Request("GET", url)
        return r
    monkeypatch.setattr(oh, "get_limited", fake)
    fetch = oh.paced_get(None, delay=3, sleep=slept.append)
    assert fetch("https://www.example.org/a") == "ok" and slept == [7, 3, 60, 3]   # Retry-After, then the fixed wait
    assert fetch("https://www.example.org/b") == "second" and slept[-1] == 3
    with pytest.raises(httpx.HTTPStatusError):
        fetch("https://www.example.org/c")
    assert len(asked) == 5


def test_the_api_injects_it_for_the_head_of_an_office_only(tmp_path, monkeypatch):
    from api import rag
    from scripts.council_sessions import upsert_fact
    path = tmp_path / "facts" / "known_facts_dynamic.json"
    fact = oh.build_fact(units(), TODAY, [INDEX, AREAS], "Comune di Esempio", extra_words={"tari"})
    assert upsert_fact(path, fact) is True
    monkeypatch.setattr(rag, "KNOWN_FACTS_DYNAMIC_FILE", path)
    monkeypatch.setattr(rag, "_DYNAMIC_FACTS", {"facts": [], "mtime": None, "checked": float("-inf")})
    titles = lambda q: [c["title"] for c in rag._inject_known_facts(q)]
    assert fact["title"] in titles("Marco Rossi è ancora il responsabile del Servizio Tributi?")
    assert fact["title"] in titles("chi è il capo dell'ufficio TARI?")
    assert fact["title"] in titles("Chi è il responsabile della TARI?")
    assert fact["title"] in titles("who is the head of the tax office?")
    assert fact["title"] not in titles("Quando si paga la TARI?")
    assert fact["title"] not in titles("Orari dell'ufficio tributi")
    # other roles, even when a word of theirs is also the name of an office («Protezione civile»)
    assert fact["title"] not in titles("Chi è il responsabile della protezione dei dati dell'ufficio tributi?")
    assert fact["title"] not in titles("Chi è il responsabile del procedimento per la TARI?")
