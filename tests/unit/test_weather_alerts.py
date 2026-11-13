"""The alerts job: the alert news of the last days become one known fact that says which alerts are
about today, which about the next days and which are past, and never «there is no alert»."""
import re
from datetime import date

from scripts import weather_alerts as wa

TODAY = date(2026, 11, 13)
PATTERN = re.compile(wa.DEFAULT_TITLE_PATTERN, re.IGNORECASE)


def item(title, day, text="La Protezione civile ha emesso un'allerta.", channel="site", url=None):
    return {"title": title, "date": day, "text": text, "channel": channel,
            "url": url or "https://news.example.it/" + re.sub(r"\W+", "-", title.lower())}


def days(title, published):
    return [d.isoformat() for d in wa.alert_days(title, date.fromisoformat(published))]


def test_the_days_an_alert_is_about_come_from_its_title():
    assert days("Allerta gialla per criticità idrogeologica e vento per venerdì 13 novembre", "2026-11-12") == ["2026-11-13"]
    assert days("Allerta gialla per temporali per venerdì 18 e sabato 19 settembre", "2026-09-17") == ["2026-09-18", "2026-09-19"]
    assert days("Allerta per il 18, 19 e 20 settembre", "2026-09-17") == ["2026-09-18", "2026-09-19", "2026-09-20"]
    assert days("Ondata di calore, livello rosso da giovedì 27 a sabato 29 agosto", "2026-08-27") == [
        "2026-08-27", "2026-08-28", "2026-08-29"]
    assert days("Bollettino calore, allerta rossa fino a domenica 19 luglio", "2026-07-17") == [
        "2026-07-17", "2026-07-18", "2026-07-19"]
    assert days("Allerta gialla per temporali dalle 14 del 1° luglio", "2026-07-01") == ["2026-07-01"]
    assert days("Allerta neve dal 30 dicembre al 2 gennaio", "2026-12-29") == [
        "2026-12-30", "2026-12-31", "2027-01-01", "2027-01-02"]
    assert days("Bollettino calore, livello arancione per oggi giovedì", "2025-06-26") == ["2025-06-26"]
    assert days("Allerta meteo arancione, il Comune attiva il COC", "2025-03-26") == []


def test_only_titles_that_say_alert_and_undated_ones_count_for_their_day():
    assert wa.to_alert(item("Test di comunicazione per maremoto, sistema di allertamento", "2026-11-10"), PATTERN) is None
    undated = wa.to_alert(item("Allerta meteo arancione, il Comune attiva il COC", "2026-11-13"), PATTERN)
    assert undated["undated"] and undated["days"] == [TODAY]
    from_text = wa.to_alert(item("Maltempo, allerta prorogata", "2026-11-12",
                                 text="L'allerta gialla è stata prorogata alla giornata di venerdì 13 novembre."), PATTERN)
    assert from_text["days"] == [TODAY] and not from_text["undated"]


def alerts(items, today=TODAY):
    return wa.classify([a for a in (wa.to_alert(i, PATTERN) for i in items) if a], today)


ITEMS = [
    item("Allerta gialla per criticità idrogeologica e vento per venerdì 13 novembre", "2026-11-12",
         text="Raffiche fino a burrasca forte (75-88 km/h). Mare molto mosso."),
    item("Allerta gialla per vento per giovedì 12 novembre", "2026-11-11"),
    item("Allerta gialla per neve per sabato 14 novembre", "2026-11-13"),
    item("Aperte le iscrizioni al corso di nuoto", "2026-11-12"),
]


def test_the_alert_for_today_is_told_apart_from_yesterdays_and_tomorrows():
    found = alerts(ITEMS)
    assert [a["title"] for a in found["today"]] == [ITEMS[0]["title"]]
    assert [a["title"] for a in found["next"]] == [ITEMS[2]["title"]]
    assert [a["title"] for a in found["past"]] == [ITEMS[1]["title"]]
    fact = wa.build_fact(found, TODAY, "Comune di Esempio", authority="Protezione Civile della Regione",
                         authority_url="https://allerte.example.it/")
    text = fact["text"]
    today_part = text.split("ALLERTE PER I PROSSIMI GIORNI")[0]
    assert "VENERDÌ 13 NOVEMBRE 2026" in today_part and "burrasca forte (75-88 km/h)" in today_part
    assert ITEMS[1]["title"] not in today_part and ITEMS[2]["title"] not in today_part
    assert "sabato 14 novembre 2026" in text and "NON riguardano oggi" in text
    assert "Protezione Civile della Regione (https://allerte.example.it/)" in text and "112" in text
    assert fact["id"] == "alerts-today" and fact["source"] == ITEMS[0]["url"] and fact["updated"] == "2026-11-13"


def test_no_alert_published_is_never_no_alert():
    fact = wa.build_fact(alerts(ITEMS[1:2] + ITEMS[3:]), TODAY, "Comune di Esempio", news_url="https://news.example.it/")
    text = fact["text"]
    assert "NON ce n'è nessuno che riguardi oggi" in text
    assert "NON significa che oggi non ci siano allerte" in text
    assert "protezione civile regionale" in text and fact["source"] == "https://news.example.it/"
    assert "Testo dell'avviso" not in text   # a past alert is listed by title only


def test_an_alert_over_several_days_is_about_each_of_them():
    found = alerts([item("Ondata di calore, livello rosso da giovedì 12 a sabato 14 novembre", "2026-11-11")])
    assert len(found["today"]) == 1 and "da giovedì 12 novembre a sabato 14 novembre 2026" in wa.build_fact(
        found, TODAY, "Comune di Esempio")["text"]


TELEGRAM = """<html><body><div class="tgme_channel_info_header_title">Comune di Esempio</div>
<div class="tgme_widget_message" data-post="comuneesempio/10"><div class="tgme_widget_message_text">⚠️ L'allerta gialla
per vento e mareggiate è stata prorogata dalla Protezione civile alla giornata di venerdì 13 novembre.</div>
<time datetime="2026-11-12T18:00:00+01:00"></time></div>
<div class="tgme_widget_message" data-post="comuneesempio/11"><div class="tgme_widget_message_text">Allerta gialla per
vento per giovedì 12 novembre <a href="https://www.example.it/news/allerta">leggi</a></div>
<time datetime="2026-11-11T14:00:00+01:00"></time></div>
<div class="tgme_widget_message" data-post="comuneesempio/3"><div class="tgme_widget_message_text">Allerta gialla per
temporali per lunedì 12 ottobre, senza altri dettagli</div><time datetime="2026-10-11T14:00:00+01:00"></time></div>
</body></html>"""


def test_a_notice_posted_only_on_the_channel_is_read(monkeypatch):
    monkeypatch.setattr(wa, "_attachment_hosts", lambda: {"www.example.it"})
    items = wa.telegram_items(TELEGRAM, date(2026, 11, 3))
    assert [i["url"] for i in items] == ["https://t.me/comuneesempio/10"]   # not the announcement, not the old one
    found = alerts(items)
    assert found["today"][0]["channel"] == "telegram"
    assert "pubblicata sul canale Telegram il 12/11/2026" in wa.build_fact(found, TODAY, "Comune di Esempio")["text"]


def test_the_api_injects_the_fact_and_forgets_the_answers_cached_before_it(tmp_path, monkeypatch):
    from api import rag
    from scripts.council_sessions import upsert_fact
    path = tmp_path / "facts" / "known_facts_dynamic.json"
    monkeypatch.setattr(rag, "KNOWN_FACTS_DYNAMIC_FILE", path)
    monkeypatch.setattr(rag, "_DYNAMIC_FACTS", {"facts": [], "mtime": None, "checked": float("-inf")})
    monkeypatch.setattr(rag, "_cache_facts_mtime", [None])
    rag._response_cache.clear()
    rag._cache_set("sono segnalate allerte per oggi?", {"answer": "nessun avviso pubblicato"})
    assert rag._cache_get("Sono segnalate allerte per oggi?")
    fact = wa.build_fact(alerts(ITEMS), TODAY, "Comune di Esempio")
    assert upsert_fact(path, fact) is True
    rag._DYNAMIC_FACTS["checked"] = float("-inf")   # the minute between two checks of the file has passed
    assert rag._cache_get("Sono segnalate allerte per oggi?") is None
    titles = lambda q: [c["title"] for c in rag._inject_known_facts(q)]
    assert fact["title"] in titles("Sono segnalate allerte per oggi?")
    assert fact["title"] in titles("Neanche allerte vento?")
    assert fact["title"] in titles("C'è maltempo domani?")
    assert fact["title"] not in titles("Come posso fare la carta d'identità?")
