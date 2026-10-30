"""The events job: the answer of The Events Calendar REST API becomes one known fact with the dates, place,
page and description of each event still in progress or coming up, which the API picks up without a restart."""
from datetime import date, datetime

_dt = datetime.fromisoformat   # wall-clock times, as the calendar gives them

from scripts import events_calendar as ec

CAL = "https://tourism.example.org/events/"
API = "https://tourism.example.org/wp-json/tribe/events/v1/events"


def _event(title, start, end, all_day=True, **kw):
    return {"title": title, "start_date": start, "end_date": end, "all_day": all_day, "status": "publish",
            "url": f"https://tourism.example.org/evento/{title.lower().replace(' ', '-')}/",
            "venue": {"venue": "Pineta Buozzi", "address": "Viale Buozzi"}, "cost": "",
            "description": "<p>Il 31 ottobre <strong>dalle 18:00 alle 24:00</strong>.</p>"
                           "<ul><li>ingresso gratuito</li></ul><p>Scarica la locandina dell&#8217;evento.</p>", **kw}


ITEMS = [
    _event("Halloween Tour", "2026-10-31 00:00:00", "2026-11-01 23:59:59"),
    _event("Rassegna Cinema", "2026-10-11 00:00:00", "2027-04-04 23:59:59", venue=[]),
    _event("Escursione", "2026-11-08 09:00:00", "2026-11-08 13:00:00", all_day=False, cost="Gratuito"),
    _event("Finito ieri", "2026-10-20 00:00:00", "2026-10-29 23:59:59"),
    _event("Nascosto", "2026-11-02 00:00:00", "2026-11-02 23:59:59", hide_from_listings=True),
    _event("Bozza", "2026-11-03 00:00:00", "2026-11-03 23:59:59", status="draft"),
]
TODAY, HORIZON = date(2026, 10, 30), date(2026, 12, 29)


def test_when_formats():
    assert ec.when(_dt("2026-10-31 00:00"), _dt("2026-11-01 23:59:59"), True) == \
        "da sabato 31 ottobre a domenica 1 novembre 2026"
    assert ec.when(_dt("2026-11-08 09:00"), _dt("2026-11-08 13:00"), False) == \
        "domenica 8 novembre 2026, dalle 9:00 alle 13:00"
    assert ec.when(_dt("2026-10-11 00:00"), _dt("2027-04-04 23:59:59"), True) == \
        "da domenica 11 ottobre 2026 a domenica 4 aprile 2027"
    # a whole day written as 00:00-23:59 without the all-day flag
    assert ec.when(_dt("2026-11-02 00:00"), _dt("2026-11-02 23:59:59"), False) == "lunedì 2 novembre 2026"


def test_parse_keeps_listed_events_not_ended():
    titles = [e["title"] for e in ec.parse_events(ITEMS, TODAY)]
    assert titles == ["Rassegna Cinema", "Halloween Tour", "Escursione"]


def test_fact_text():
    fact = ec.build_fact(ec.parse_events(ITEMS, TODAY), TODAY, HORIZON, CAL, "Comune di Esempio")
    text = fact["text"]
    assert "letto automaticamente venerdì 30 ottobre 2026" in text
    # exhibitions open over a period count for every day of it, a series only on the days of its programme
    assert "3) le mostre e le altre iniziative aperte per un periodo («fino al 31 dicembre»" in text
    assert "Una rassegna che in quei giorni non ha appuntamenti non va presentata come evento di quei giorni" in text
    # an exhibition is a «manifestazione» too, and no event is «the only one» while the list has others
    assert "Tutte e tre contano come eventi e manifestazioni" in text
    assert "non dire che un evento è l'unico dei giorni chiesti" in text
    assert ("EVENTO: Halloween Tour\nQuando: da sabato 31 ottobre a domenica 1 novembre 2026\n"
            "Dove: Pineta Buozzi, Viale Buozzi\nPagina dell'evento: https://tourism.example.org/evento/halloween-tour/\n"
            "Descrizione:\nIl 31 ottobre dalle 18:00 alle 24:00.\n- ingresso gratuito\n") in text
    assert "Scarica" not in text
    assert ("Quando: da domenica 11 ottobre 2026 a domenica 4 aprile 2027 (già iniziato, in corso; periodo "
            "complessivo") in text
    assert "Quando: domenica 8 novembre 2026, dalle 9:00 alle 13:00\nDove: Pineta Buozzi, Viale Buozzi\nCosto: Gratuito" in text
    assert text.rstrip().endswith(f"Calendario completo degli eventi, anche dei mesi successivi: {CAL}")
    assert fact["id"] == ec.FACT_ID and fact["source"] == CAL
    assert "weekend" in fact["keywords"] and "weekend" in fact["require_any"]


def test_fact_without_events():
    text = ec.build_fact([], TODAY, HORIZON, CAL, "Comune di Esempio")["text"]
    assert "Al 30/10/2026 il calendario non riporta eventi in corso né eventi che iniziano entro martedì 29 dicembre 2026." in text


def test_description_is_cut_at_a_line_end():
    raw = "".join(f"<p>Riga numero {i} del programma</p>" for i in range(50))
    out = ec.clean_description(raw, 120)
    assert out.splitlines()[0] == "Riga numero 0 del programma"
    assert out.endswith("[…] (il resto nella pagina dell'evento)")
    assert len(out) < 200


def test_read_events_follows_the_pages():
    asked = []

    def fake(url):
        asked.append(url)
        page = int(url.rsplit("page=", 1)[1])
        return {"events": [ITEMS[page - 1]], "total": 2, "total_pages": 2}

    items = ec.read_events(API, TODAY, HORIZON, fetch=fake)
    assert [i["title"] for i in items] == ["Halloween Tour", "Rassegna Cinema"]
    assert len(asked) == 2
    assert "ends_after=2026-10-30+00%3A00%3A00" in asked[0] and "starts_before=2026-12-29+23%3A59%3A59" in asked[0]


def test_the_api_picks_the_fact_up(tmp_path, monkeypatch):
    from api import rag
    from scripts.council_sessions import upsert_fact
    path = tmp_path / "facts" / "known_facts_dynamic.json"
    fact = ec.build_fact(ec.parse_events(ITEMS, TODAY), TODAY, HORIZON, CAL, "Comune di Esempio")
    assert upsert_fact(path, fact) is True
    monkeypatch.setattr(rag, "KNOWN_FACTS_DYNAMIC_FILE", path)
    # -inf, not 0.0: on a machine up for less than a minute monotonic() is below the check interval
    monkeypatch.setattr(rag, "_DYNAMIC_FACTS", {"facts": [], "mtime": None, "checked": float("-inf")})
    titles = lambda q: [c["title"] for c in rag._inject_known_facts(q)]
    assert fact["title"] in titles("Cosa c'è da fare questo weekend?")
    assert fact["title"] in titles("Quali eventi ci sono questo fine settimana?")
    assert fact["title"] in titles("Cosa posso fare questo fine settimana?")
    assert fact["title"] not in titles("Come posso fare la carta d'identità?")
    assert fact["title"] not in titles("Orari dell'anagrafe della settimana prossima")
