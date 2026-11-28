"""
The known facts whose date is near or past, mailed to who keeps them.

A curated fact (config/known_facts.json) stays true only until the page behind it changes, and
nothing told anybody when to look at it again: a fact saying «the Christmas programme has not been
published yet» was still answered the day after the programme came out. A fact may now carry two
dates (api/knownfacts.py): "expires", the last day it is used, and "review_by", the day by which
somebody checks it again. This script reads the file and lists

  - the facts that have expired: the API no longer uses them, they are to be removed or rewritten;
  - the facts that expire within KNOWN_FACTS_REPORT_DAYS days;
  - the facts to check again within those days, and the ones whose check is overdue;
  - the dates that are not dates (the API uses those facts as if they had none).

It keeps no state. A mail is sent on the days something happens to a fact (it enters the list,
its last day is tomorrow, it expired yesterday, its check is due today), every day while a date
is mistyped, and every Monday while a fact is expired or a check is overdue: a reminder that has
been read and put aside comes back until the file is edited. On the other days nothing is sent.

Run (daily from cron, as root: the SMTP settings are in .env; it reads only the installation's
own file, nothing from the Internet):
    venv/bin/python -m scripts.known_facts_report [--dry-run] [--force] [--today YYYY-MM-DD]
Exit codes: 0 sent or nothing to send, 1 file unreadable, no address or send error.
"""

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.knownfacts import DATE_FIELDS, KNOWN_FACTS_FILE, fact_date
from config.settings import (
    KNOWN_FACTS_REPORT_DAYS,
    KNOWN_FACTS_REPORT_TO,
    SITE_NAME,
    now_local,
)

log = logging.getLogger("known_facts_report")

MAX_TITLE = 120
# the order of the sections of the mail
KINDS = ("bad_date", "expired", "expiring", "review_overdue", "review_soon")
_HEADINGS = {
    "bad_date": "Date scritte male",
    "expired": "Scaduti: Zautte non li usa più",
    "expiring": "In scadenza",
    "review_overdue": "Da ricontrollare: data passata",
    "review_soon": "Da ricontrollare a breve",
}
_TODO = {
    "bad_date": "Zautte usa il fatto come se la data non ci fosse: correggerla (AAAA-MM-GG).",
    "expired": "Da fare: toglierlo dal file dei fatti noti, oppure riscriverlo e cambiare la data.",
    "expiring": "Dal giorno dopo Zautte non lo userà più: se serve ancora, riscriverlo o spostare la data.",
    "review_overdue": "Zautte continua a usarlo: verificare sulla fonte che sia ancora vero, poi spostare la data.",
    "review_soon": "Zautte continua a usarlo: verificare sulla fonte che sia ancora vero, poi spostare la data.",
}


def read_entries(path: Path) -> list[dict]:
    """The facts of the file. OSError, ValueError or TypeError when it cannot be read or is not a list."""
    entries = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        raise TypeError("not a list of facts")
    return [e for e in entries if isinstance(e, dict)]


def _title(entry: dict) -> str:
    title = " ".join(str(entry.get("title", "")).split()) or "(senza titolo)"
    return title if len(title) <= MAX_TITLE else title[:MAX_TITLE - 1].rstrip() + "…"


def open_items(entries: list[dict], today: date, days: int) -> list[dict]:
    """What is to be looked at: one item per date of a fact that is mistyped, past, or within `days`
    days. `left` is the number of days from today to the date (negative when it is past)."""
    items = []
    for entry in entries:
        for field in DATE_FIELDS:
            base = {"field": field, "title": _title(entry), "source": str(entry.get("source", ""))}
            try:
                when = fact_date(entry, field)
            except ValueError:
                items.append({**base, "kind": "bad_date", "value": entry.get(field), "date": None, "left": 0})
                continue
            if when is None:
                continue
            left = (when - today).days
            if field == "expires":
                kind = "expired" if left < 0 else "expiring" if left <= days else None
            else:
                kind = "review_overdue" if left < 0 else "review_soon" if left <= days else None
            if kind:
                items.append({**base, "kind": kind, "date": when, "left": left})
    return sorted(items, key=lambda i: (KINDS.index(i["kind"]), i["date"] or date.min, i["title"]))


def is_milestone(item: dict, days: int) -> bool:
    """Whether today something happens to the item that is worth a mail by itself."""
    if item["kind"] == "bad_date":
        return True
    if item["field"] == "expires":
        return item["left"] in (days, 1, -1)      # enters the list, tomorrow is the last day, out since today
    return item["left"] in (days, 0)              # enters the list, the check is due today


def should_send(items: list[dict], today: date, days: int) -> bool:
    if any(is_milestone(i, days) for i in items):
        return True
    # Monday: what is still open comes back until the file is edited
    return today.weekday() == 0 and any(i["kind"] in ("expired", "review_overdue") for i in items)


def _when(item: dict) -> str:
    left, day = item["left"], f"{item['date']:%d/%m/%Y}"
    if item["kind"] == "expired":
        return f"scaduto il {day} ({'ieri' if left == -1 else f'da {-left} giorni'})"
    if item["kind"] == "expiring":
        return f"ultimo giorno: {day} ({'oggi' if left == 0 else 'domani' if left == 1 else f'tra {left} giorni'})"
    if item["kind"] == "review_overdue":
        return f"era da ricontrollare entro il {day} ({'ieri' if left == -1 else f'da {-left} giorni'})"
    return f"da ricontrollare entro il {day} ({'oggi' if left == 0 else 'domani' if left == 1 else f'tra {left} giorni'})"


def build_report(items: list[dict], today: date, site: str, days: int) -> tuple[str, str]:
    """(subject, body) of the mail: the items by kind, each with its date and what to do."""
    lines = [f"Fatti noti di Zautte con una data vicina o passata, al {today:%d/%m/%Y}: {len(items)}.", ""]
    for kind in KINDS:
        group = [i for i in items if i["kind"] == kind]
        if not group:
            continue
        heading = _HEADINGS[kind] + (f" (entro {days} giorni)" if kind in ("expiring", "review_soon") else "")
        lines.append(f"== {heading}: {len(group)}")
        for i in group:
            lines.append(f"- «{i['title']}»")
            if kind == "bad_date":
                lines.append(f"  \"{i['field']}\": {json.dumps(i['value'], ensure_ascii=False)} non è una data")
            else:
                lines.append(f"  {_when(i)}")
            if i["source"]:
                lines.append(f"  fonte: {i['source']}")
        lines += [f"  {_TODO[kind]}", ""]
    lines.append(f"Elenco automatico di Zautte ({site}). Le date si scrivono nel file dei fatti noti: "
                 "\"expires\" è l'ultimo giorno in cui il fatto viene usato, \"review_by\" il giorno entro cui "
                 "ricontrollarlo. Questo messaggio arriva quando una data si avvicina o passa, e ogni lunedì "
                 "finché resta un fatto scaduto o un controllo in ritardo.")
    urgent = sum(1 for i in items if i["kind"] in ("bad_date", "expired", "review_overdue"))
    subject = (f"Zautte: {len(items)} fatti noti da rivedere" if len(items) != 1 else "Zautte: 1 fatto noto da rivedere")
    if urgent:
        subject += f" ({urgent} già scaduti o in ritardo)" if urgent != 1 else " (1 già scaduto o in ritardo)"
    return subject, "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Known facts whose date is near or past → mail")
    ap.add_argument("--dry-run", action="store_true", help="print the mail, send nothing")
    ap.add_argument("--force", action="store_true", help="send (or print) the list even on a day without news")
    ap.add_argument("--today", type=date.fromisoformat, help="pretend today is this day (YYYY-MM-DD)")
    ap.add_argument("--days", type=int, default=KNOWN_FACTS_REPORT_DAYS, help="days before a date")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    today = args.today or now_local().date()
    try:
        entries = read_entries(KNOWN_FACTS_FILE)
    except FileNotFoundError:
        log.info("no %s: nothing to report", KNOWN_FACTS_FILE.name)
        return 0
    except (OSError, ValueError, TypeError) as exc:
        log.error("%s cannot be read: %s", KNOWN_FACTS_FILE.name, exc)
        return 1
    items = open_items(entries, today, args.days)
    dated = sum(1 for e in entries if any(e.get(f) for f in DATE_FIELDS))
    if not items:
        log.info("%d facts, %d with a date: none near or past, nothing sent", len(entries), dated)
        return 0
    if not (args.force or should_send(items, today, args.days)):
        log.info("%d facts, %d with a date: %d in the list, nothing new today, nothing sent",
                 len(entries), dated, len(items))
        return 0
    subject, body = build_report(items, today, SITE_NAME, args.days)
    if args.dry_run:
        print(subject, "\n\n", body)
        return 0
    if not KNOWN_FACTS_REPORT_TO:
        log.error("KNOWN_FACTS_REPORT_TO (or FEEDBACK_NOTIFY_EMAIL) is not set: %d items not reported", len(items))
        return 1
    from api.mailer import send_email
    try:
        send_email(KNOWN_FACTS_REPORT_TO, subject, body)
    except Exception as exc:  # noqa: BLE001 - any SMTP error: say it and stop
        log.error("report not sent: %s", exc)
        return 1
    log.info("report sent to %s: %d items", KNOWN_FACTS_REPORT_TO, len(items))
    return 0


if __name__ == "__main__":
    sys.exit(main())
