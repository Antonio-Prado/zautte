"""scripts/acts_sync.py: the registers of acts of a Nuvola portal, one passage per act."""

from datetime import date

import httpx

from scripts import acts_sync

PORTAL = "https://atti.example.it/"
REGISTER = {"page_id": 35564, "name": "Delibera di Giunta comunale", "plural": "delibere di Giunta comunale"}
CONFIG = {"portal": PORTAL, "years": 2, "latest": 2, "registers": [REGISTER]}
TODAY = date(2026, 10, 28)


def _row(number: int, when: str, subject: str, published: str) -> str:
    return (f"<tr> <td>{number:012d}</td> <td>{when}</td> <td class='ogg_table'><div>"
            f"<span style='font-weight: bold;'>Delibera di Giunta</span><br>{subject}</div></td> "
            f"<td>Comune di Esempio</td> <td>{published}</td><td class='align-center'>"
            f"<a href='{PORTAL}?page_id=35564&pub_token=abc'>x</a></td></tr>")


def _page(*rows: str) -> str:
    head = "<tr> <th>Atto</th> <th>Data</th> <th>Oggetto</th> <th>Ente</th> <th>Pubbl.</th><th>Dettaglio</th></tr>"
    return f"<html><body><table class='paginated'>{head}{''.join(rows)}</table></body></html>"


PAGE_2026 = _page(
    _row(151, "23/10/2026", "INIZIATIVE CULTURALI AUTUNNO 2026", "27/10/2026"),
    _row(150, "16/10/2026", "PROGETTO SBT CHRISTMAS VILLAGE 2026/2027", "20/10/2026"),
    _row(146, "08/10/2026", "INTERVENTI DI ACCOGLIENZA TURISTICA AUTUNNO 2026", "12/10/2026"),
)
PAGE_2025 = _page(_row(200, "30/12/2025", "BILANCIO DI PREVISIONE", "31/12/2025"))


def _fetch(pages: dict):
    def fetch(page_id, year):
        page = pages[year]
        if isinstance(page, Exception):
            raise page
        return page
    return fetch


def test_parse_acts_reads_the_rows_and_skips_the_header():
    acts = acts_sync.parse_acts(PAGE_2026 + "<tr><td>totale</td><td>3</td><td>x</td><td>y</td><td>z</td></tr>")
    assert [a["number"] for a in acts] == [151, 150, 146]
    assert acts[0] == {"number": 151, "date": "2026-10-23", "kind": "Delibera di Giunta",
                       "subject": "INIZIATIVE CULTURALI AUTUNNO 2026", "published": "2026-10-27"}


def test_each_act_is_a_passage_with_number_date_subject_and_where_to_find_it():
    docs, removed, _, counts = acts_sync.collect(_fetch({2026: PAGE_2026, 2025: PAGE_2025}), CONFIG,
                                                  {"items": {}}, TODAY)
    assert counts["new"] == 4 and counts["acts"] == 4 and not removed
    doc = next(d for d in docs if d["url"].endswith("#atto-2026-151"))
    assert doc["url"] == f"{PORTAL}?page_id=35564#atto-2026-151"
    chunk = doc["chunks"][0]
    assert "Delibera di Giunta comunale n. 151 del 23 ottobre 2026" in chunk["text"]
    assert "INIZIATIVE CULTURALI AUTUNNO 2026" in chunk["text"]
    assert f"{PORTAL}?page_id=35564), indicando il numero 151 e l'anno 2026" in chunk["text"]
    meta = chunk["metadata"]
    assert meta["origin"] == "acts" and meta["date"] == "2026-10-23" and meta["category"] == "atto"
    assert meta["title"].startswith("Delibera di Giunta comunale n. 151 del 23/10/2026: INIZIATIVE")


def test_a_second_run_with_nothing_new_indexes_nothing():
    fetch = _fetch({2026: PAGE_2026, 2025: PAGE_2025})
    _, _, state, _ = acts_sync.collect(fetch, CONFIG, {"items": {}}, TODAY)
    docs, removed, _, counts = acts_sync.collect(fetch, CONFIG, state, TODAY)
    assert docs == [] and removed == set() and counts["new"] == counts["changed"] == 0


def test_a_changed_subject_is_reindexed_and_a_vanished_act_removed():
    _, _, state, _ = acts_sync.collect(_fetch({2026: PAGE_2026, 2025: PAGE_2025}), CONFIG, {"items": {}}, TODAY)
    page = _page(_row(151, "23/10/2026", "INIZIATIVE CULTURALI AUTUNNO 2026 (RETTIFICA)", "27/10/2026"),
                 _row(150, "16/10/2026", "PROGETTO SBT CHRISTMAS VILLAGE 2026/2027", "20/10/2026"))
    docs, removed, state, counts = acts_sync.collect(_fetch({2026: page, 2025: PAGE_2025}), CONFIG, state, TODAY)
    assert counts["changed"] == 1 and [d["url"] for d in docs] == [f"{PORTAL}?page_id=35564#atto-2026-151"]
    assert removed == {f"{PORTAL}?page_id=35564#atto-2026-146"}
    assert f"{PORTAL}?page_id=35564#atto-2026-146" not in state["items"]


def test_a_register_not_read_or_suddenly_empty_keeps_its_acts():
    _, _, state, _ = acts_sync.collect(_fetch({2026: PAGE_2026, 2025: PAGE_2025}), CONFIG, {"items": {}}, TODAY)
    for broken in (httpx.ConnectError("down"), _page()):
        _, removed, new_state, counts = acts_sync.collect(
            _fetch({2026: broken, 2025: PAGE_2025}), CONFIG, state, TODAY)
        assert removed == set() and counts["failed"] == 1
        assert new_state["items"] == state["items"]


def test_latest_fact_lists_the_last_acts_of_each_register():
    _, _, state, _ = acts_sync.collect(_fetch({2026: PAGE_2026, 2025: PAGE_2025}), CONFIG, {"items": {}}, TODAY)
    fact = acts_sync.latest_fact(CONFIG, state["items"], TODAY)
    assert fact["id"] == "acts-latest"
    text = fact["text"]
    assert "n. 151 del 23/10/2026: INIZIATIVE CULTURALI AUTUNNO 2026" in text
    assert "n. 150 del 16/10/2026" in text and "n. 146" not in text   # latest = 2
    assert "controllato il 28/10/2026" in text
    assert "ultime" in fact["require_any"] and "delibere" in fact["keywords"]
