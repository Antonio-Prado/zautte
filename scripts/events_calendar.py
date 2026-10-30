"""
Events in progress and coming up, kept up to date by cron.

"What is on this weekend?" needs today's date and the dates of the events, which a search by
similarity cannot match: no page says «questo weekend». Many tourism portals run on WordPress with
The Events Calendar, whose public REST API (/wp-json/tribe/events/v1/events) lists the events with
their dates, place and description. This job reads the events still in progress today and those
starting within `days_ahead` days, and writes them as one known fact in KNOWN_FACTS_DYNAMIC_FILE
(same format as config/known_facts.json). The API rereads that file when it changes: no restart.

Configuration: the "events" key of config/crawl_extra.json
    {"api_url": "https://tourism.example.org/wp-json/tribe/events/v1/events",
     "calendar_url": "https://tourism.example.org/events/",   # the calendar page, linked in the fact
     "days_ahead": 60,                  # events starting up to this many days from today (default)
     "max_description": 4000,           # characters of each description kept (default)
     "keywords": [...], "require_any": [...]}   # optional: when the fact is injected
Run (hourly from cron, as the sync account):
    venv/bin/python -m scripts.events_calendar [--dry-run]
Exit codes: 0 written or unchanged, 1 fetch or configuration error (the previous file is kept).
"""

import argparse
import html
import json
import logging
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.privdrop import drop_to_sync_user

drop_to_sync_user()   # as root: become the sync account before config.settings reads .env

import httpx

from config.settings import (
    CRAWL_ALLOWED_DOMAINS,
    EVENTS_CALENDAR,
    KNOWN_FACTS_DYNAMIC_FILE,
    SITE_NAME,
    now_local,
)
from crawler.netpolicy import get_limited
from scripts.council_sessions import MONTHS, WEEKDAYS, upsert_fact

log = logging.getLogger("events_calendar")

FACT_ID = "events-upcoming"
PER_PAGE = 50
MAX_PAGES = 10
LONG_PERIOD_DAYS = 7   # an event this long is a series (season, festival, course): its dates are the whole period

# Words of a question about events. A word in both lists is enough; "settimana" (only in keywords)
# needs one of "fare", "succede", "uscire" (only in require_any): «cosa posso fare questo fine settimana?»
_STRONG = [
    "eventi", "evento", "manifestazioni", "manifestazione", "spettacoli", "spettacolo", "concerti", "concerto",
    "mostre", "mostra", "sagra", "sagre", "rassegna", "rassegne", "festival", "halloween", "weekend", "stasera",
    "teatro", "teatri", "teatrale", "teatrali", "cinema", "film", "escursione", "escursioni", "intrattenimento",
    "divertimento", "divertirsi", "divertirmi", "svago", "events", "event", "concerts", "concert", "exhibition",
    "exhibitions", "tonight",
]
DEFAULT_KEYWORDS = [*_STRONG, "settimana"]
DEFAULT_REQUIRE_ANY = [*_STRONG, "fare", "succede", "uscire"]

_BLOCK_END_RE = re.compile(r"<\s*(br|/p|/div|/li|/h[1-6]|/tr)\b[^>]*>", re.IGNORECASE)
_LI_RE = re.compile(r"<\s*li\b[^>]*>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_DOWNLOAD_LINE_RE = re.compile(r"^scarica\b", re.IGNORECASE)


def clean_description(raw: str, limit: int) -> str:
    """The description as plain text, one line per paragraph or list item, without the «Scarica …»
    lines (their links are lost with the markup), cut at a line end within `limit` characters."""
    text = _LI_RE.sub("\n- ", _BLOCK_END_RE.sub("\n", raw or ""))
    text = html.unescape(_TAG_RE.sub(" ", text)).replace("\xa0", " ")
    lines = []
    for line in text.split("\n"):
        line = re.sub(r"\s+", " ", line).strip()
        line = re.sub(r"\s+([,.;:!?])", r"\1", line)
        if not line or line == "-" or (_DOWNLOAD_LINE_RE.match(line) and len(line) <= 80):
            continue
        lines.append(line)
    out, size = [], 0
    for line in lines:
        if size + len(line) + 1 > limit:
            out.append("[…] (il resto nella pagina dell'evento)")
            break
        out.append(line)
        size += len(line) + 1
    return "\n".join(out)


def _day(d: date, year: bool = True) -> str:
    out = f"{WEEKDAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]}"
    return f"{out} {d.year}" if year else out


def _hour(t: datetime) -> str:
    return f"{t.hour}:{t.minute:02d}"


def when(start: datetime, end: datetime, all_day: bool) -> str:
    """«sabato 31 ottobre 2026», «da sabato 31 ottobre a domenica 1 novembre 2026»,
    «domenica 8 novembre 2026, dalle 9:00 alle 13:00»."""
    first, last = start.date(), end.date()
    whole_days = all_day or (start.hour == start.minute == 0 and (end.hour, end.minute) == (23, 59))
    if whole_days:
        if first == last:
            return _day(first)
        return f"da {_day(first, year=first.year != last.year)} a {_day(last)}"
    if first == last:
        return f"{_day(first)}, dalle {_hour(start)} alle {_hour(end)}" if end > start else f"{_day(first)}, alle {_hour(start)}"
    return f"da {_day(first, year=first.year != last.year)} alle {_hour(start)} a {_day(last)} alle {_hour(end)}"


def _venue(event: dict) -> str:
    v = event.get("venue")
    if isinstance(v, list):
        v = v[0] if v else {}
    if not isinstance(v, dict):
        return ""
    name = html.unescape(str(v.get("venue") or "")).strip()
    parts = [name] if name else []
    for key in ("address", "city"):
        val = html.unescape(str(v.get(key) or "")).strip()
        if val and val.lower() not in name.lower():
            parts.append(val)
    return ", ".join(parts)


def parse_events(items: list[dict], today: date) -> list[dict]:
    """The listed, published events that have not ended before today, by start date."""
    events = []
    for e in items:
        if e.get("status", "publish") != "publish" or e.get("hide_from_listings"):
            continue
        try:   # wall-clock times of the calendar ("2026-10-31 18:00:00"), compared only with today's date
            start = datetime.fromisoformat(e["start_date"])
            end = datetime.fromisoformat(e.get("end_date") or e["start_date"])
        except (KeyError, TypeError, ValueError):
            continue
        if end.date() < today:
            continue
        events.append({
            "title": re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub("", str(e.get("title") or "")))).strip(),
            "start": start, "end": end, "all_day": bool(e.get("all_day")),
            "url": str(e.get("url") or ""), "venue": _venue(e),
            "cost": html.unescape(str(e.get("cost") or "")).strip(),
            "description": str(e.get("description") or ""),
        })
    events.sort(key=lambda x: (x["start"], x["title"]))
    return events


def build_fact(events: list[dict], today: date, horizon: date, calendar_url: str, site_name: str,
               max_description: int = 4000, keywords: list[str] | None = None,
               require_any: list[str] | None = None) -> dict:
    """The known fact: every event with its dates, place, page and description, and how to read them."""
    lines = [(f"Eventi in corso e in programma secondo il calendario degli eventi {calendar_url}, letto "
              f"automaticamente {_day(today)}: gli eventi ancora in corso oggi e quelli che iniziano entro "
              f"{_day(horizon)}, in ordine di inizio."),
             ("Come leggere l'elenco: per «oggi», «domani», «stasera», «questo weekend» o «questo fine "
              "settimana» confronta le date con la data di oggi. Rassegne, festival, corsi e stagioni teatrali "
              "hanno come date il periodo complessivo: guarda il programma nella descrizione. Per i giorni "
              "chiesti elenca: 1) gli eventi che cadono in quei giorni; 2) gli appuntamenti che il programma di "
              "una rassegna o di un festival mette in quei giorni; 3) le mostre e le altre iniziative aperte per "
              "un periodo («fino al 31 dicembre», «dal 16 novembre al 6 dicembre») che comprende quei giorni, "
              "anche quando fanno parte di un festival, con il luogo. Una rassegna che in quei giorni non ha "
              "appuntamenti non va presentata come evento di quei giorni: se serve, indicane a parte il "
              "prossimo appuntamento. Se il programma non dice nulla dei giorni chiesti, indica il periodo e "
              "la pagina dell'evento. "
              "Se la riga «Quando» non ha l'orario, gli orari sono quelli scritti nella descrizione, se ci sono. "
              "Il calendario non indica quali eventi hanno il patrocinio del Comune, e iniziative non "
              "pubblicate nel calendario non compaiono qui."), ""]
    if not events:
        lines.append(f"Al {today:%d/%m/%Y} il calendario non riporta eventi in corso né eventi che iniziano "
                     f"entro {_day(horizon)}.")
        lines.append("")
    for ev in events:
        period = when(ev["start"], ev["end"], ev["all_day"])
        notes = []
        if ev["start"].date() < today:
            notes.append("già iniziato, in corso")
        if (ev["end"].date() - ev["start"].date()).days >= LONG_PERIOD_DAYS:
            notes.append("periodo complessivo: le date dei singoli appuntamenti sono nella descrizione, se indicate")
        lines.append(f"EVENTO: {ev['title']}")
        lines.append(f"Quando: {period}" + (f" ({'; '.join(notes)})" if notes else ""))
        if ev["venue"]:
            lines.append(f"Dove: {ev['venue']}")
        if ev["cost"]:
            lines.append(f"Costo: {ev['cost']}")
        if ev["url"]:
            lines.append(f"Pagina dell'evento: {ev['url']}")
        desc = clean_description(ev["description"], max_description)
        if desc:
            lines.append(f"Descrizione:\n{desc}")
        lines.append("")
    lines.append(f"Calendario completo degli eventi, anche dei mesi successivi: {calendar_url}")
    return {
        "id": FACT_ID,
        "keywords": keywords or DEFAULT_KEYWORDS,
        "require_any": require_any or DEFAULT_REQUIRE_ANY,
        "title": f"Eventi in corso e in programma (calendario degli eventi, aggiornato automaticamente) - {site_name}",
        "source": calendar_url,
        "category": "eventi",
        "updated": today.isoformat(),
        "text": "\n".join(lines).rstrip() + "\n",
    }


def fetch_json(url: str) -> dict:
    with httpx.Client(timeout=30, headers={"User-Agent": "Zautte events-calendar",
                                           "Accept": "application/json"}) as client:
        r = get_limited(client, url, CRAWL_ALLOWED_DOMAINS)
        r.raise_for_status()
        return json.loads(r.text)


def read_events(api_url: str, today: date, horizon: date, fetch=fetch_json) -> list[dict]:
    """Every page of the API answer: events ending today or later and starting by the horizon."""
    items: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        query = urlencode({"ends_after": f"{today.isoformat()} 00:00:00",
                           "starts_before": f"{horizon.isoformat()} 23:59:59",
                           "per_page": PER_PAGE, "page": page})
        data = fetch(f"{api_url}{'&' if '?' in api_url else '?'}{query}")
        if not isinstance(data, dict) or not isinstance(data.get("events"), list):
            raise TypeError("unexpected answer: no list of events")
        items.extend(data["events"])
        if page >= int(data.get("total_pages") or 1):
            break
    return items


def main() -> int:
    ap = argparse.ArgumentParser(description="Events calendar (The Events Calendar REST API) → dynamic known fact")
    ap.add_argument("--dry-run", action="store_true", help="print the fact, write nothing")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    api_url = (EVENTS_CALENDAR.get("api_url") or "").strip()
    calendar_url = (EVENTS_CALENDAR.get("calendar_url") or "").strip() or api_url
    if not api_url:
        log.error("no events.api_url in config/crawl_extra.json: nothing to do")
        return 1
    today = now_local().date()
    horizon = today + timedelta(days=int(EVENTS_CALENDAR.get("days_ahead") or 60))
    try:
        items = read_events(api_url, today, horizon)
    except Exception as exc:  # noqa: BLE001 - any network/policy/format error: keep the previous file
        log.error("could not read %s: %s", api_url, exc)
        return 1
    events = parse_events(items, today)
    fact = build_fact(events, today, horizon, calendar_url, SITE_NAME,
                      int(EVENTS_CALENDAR.get("max_description") or 4000),
                      EVENTS_CALENDAR.get("keywords"), EVENTS_CALENDAR.get("require_any"))
    if args.dry_run:
        print(fact["text"])
    written = upsert_fact(KNOWN_FACTS_DYNAMIC_FILE, fact, dry_run=args.dry_run)
    log.info("%d events until %s (%d characters); %s", len(events), horizon.isoformat(), len(fact["text"]),
             "written" if written and not args.dry_run else "unchanged" if not written else "dry run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
