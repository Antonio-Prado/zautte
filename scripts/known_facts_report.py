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

and, from what scripts/known_facts_watch.py read this morning (KNOWN_FACTS_WATCH_FILE),

  - the pages the facts come from that are gone from the site, or could not be read for days;
  - the pages whose text is not what it was when their facts were last edited, with the lines added
    and taken out, and the pages that now have (or no longer have) what a fact asked to look for:
    each stays in the list until the fact that comes from that page is edited (also just its
    "review_by");
  - the watch job itself, when it has not read the pages for two days;
  - the dynamic facts a job has not written for KNOWN_FACTS_STALE_DAYS days: the job keeps the last
    fact it managed to write, which goes on being answered while the page it reads has changed.

It keeps no state. A mail is sent on the days something happens (a fact enters the list, its last
day is tomorrow, it expired yesterday, its check is due today, a page changed or went missing this
morning), every day while a date is mistyped, and every Monday while something is still open: a
reminder that has been read and put aside comes back until the file is edited. On the other days
nothing is sent.

Run (daily from cron, as root: the SMTP settings are in .env; it reads only the installation's
own file, nothing from the Internet):
    venv/bin/python -m scripts.known_facts_report [--dry-run] [--force] [--today YYYY-MM-DD]
Exit codes: 0 sent or nothing to send, 1 file unreadable, no address or send error.
"""

import argparse
import json
import logging
import re
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.knownfacts import DATE_FIELDS, KNOWN_FACTS_FILE, fact_date, watch_targets
from api.safefile import read_json_bounded
from config.settings import (
    KNOWN_FACTS_DYNAMIC_FILE,
    KNOWN_FACTS_REPORT_DAYS,
    KNOWN_FACTS_REPORT_TO,
    KNOWN_FACTS_STALE_DAYS,
    KNOWN_FACTS_WATCH_FILE,
    SITE_NAME,
    now_local,
)

log = logging.getLogger("known_facts_report")

MAX_TITLE = 120
MAX_PAGE_LINE = 200          # characters of a line of a page quoted in the mail
MAX_PAGE_LINES = 6           # lines added, and lines taken out, quoted for a page
WATCH_FAILS = 3              # readings in a row a page may fail before it is listed (as in the watch job)
_WATCH_STATE_MAX_BYTES = 64 * 1024 * 1024
_DYNAMIC_MAX_BYTES = 4 * 1024 * 1024
# the order of the sections of the mail
KINDS = ("bad_date", "expired", "page_gone", "page_appears", "page_disappears", "page_changed", "expiring",
         "review_overdue", "review_soon", "stale", "page_unread", "watch_stopped")
# what is still to be done after the first mail: these come back every Monday
OPEN_KINDS = ("expired", "review_overdue", "page_gone", "page_appears", "page_disappears", "page_changed",
              "stale", "page_unread", "watch_stopped")
URGENT_KINDS = ("bad_date", "expired", "review_overdue", "page_gone")
_HEADINGS = {
    "bad_date": "Date scritte male",
    "expired": "Scaduti: Zautte non li usa più",
    "expiring": "In scadenza",
    "review_overdue": "Da ricontrollare: data passata",
    "review_soon": "Da ricontrollare a breve",
    "page_gone": "Pagine di origine che non esistono più",
    "page_appears": "Pagine dove è comparso ciò che un fatto aspettava",
    "page_disappears": "Pagine dove non c'è più ciò su cui un fatto si basa",
    "page_changed": "Pagine di origine cambiate",
    "page_unread": "Pagine di origine che non si riescono a leggere",
    "stale": "Schede automatiche ferme",
    "watch_stopped": "Il controllo delle pagine di origine non sta girando",
}
_TODO = {
    "page_gone": "Zautte continua a usare questi fatti e a mostrare un link che non funziona: indicare una pagina "
                 "esistente come fonte, oppure togliere ciò che non ha più una fonte.",
    "page_appears": "Il fatto dice che una cosa non c'è ancora, e ora c'è: riscriverlo o toglierlo.",
    "page_disappears": "Il fatto si basa su qualcosa che la pagina non dice più: verificarlo e correggerlo.",
    "page_changed": "Zautte continua a usare questi fatti: confrontarli con la pagina; se sono ancora giusti basta "
                    "spostare la loro data di ricontrollo (\"review_by\"), altrimenti correggerli. In entrambi i "
                    "casi la voce esce da questo elenco.",
    "page_unread": "Può essere un guasto del sito: se dura, controllare l'indirizzo della fonte.",
    "watch_stopped": "Le pagine non vengono rilette: guardare il cron e il log di known_facts_watch.",
    "stale": "Il job che la scrive non ci riesce più (di solito la pagina che legge è cambiata): Zautte continua a "
             "usare l'ultima scheda scritta. Guardare il log del job.",
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


def _clean(line: object) -> str:
    """A line of a page as it goes into the mail: printable, on one line, not too long."""
    text = "".join(ch if ch.isprintable() else " " for ch in str(line))
    return " ".join(text.split())[:MAX_PAGE_LINE]


def watch_items(entries: list[dict], state: dict, today: date) -> list[dict]:
    """What the watch job found on the pages the facts come from and is still open: the pages gone
    or not read, the pages whose text is not what it was when their facts were last edited, the
    conditions of the facts that hold. An item is new (worth a mail by itself) when it dates from
    today or yesterday: the job may run after the report of the day."""
    items = []
    recent = {today.isoformat(), (today - timedelta(days=1)).isoformat()}
    targets = watch_targets(entries)
    checked = [str(r.get("checked", "")) for u, r in state.items() if u in targets and isinstance(r, dict)]
    checked = [day for day in checked if _DAY_RE.fullmatch(day)]
    if checked and max(checked) < min(recent):
        age = _age(max(checked), today)
        items.append({"kind": "watch_stopped", "titles": [], "url": "", "day": max(checked), "age": age,
                      "new": age == 2, "date": None, "left": 0})
    for url, target in targets.items():
        record = state.get(url)
        if not isinstance(record, dict):
            continue
        base = {"url": url, "titles": [_title({"title": t}) for t in target["titles"]], "date": None, "left": 0}
        if record.get("gone_since"):
            items.append({**base, "kind": "page_gone", "day": str(record["gone_since"]),
                          "new": record["gone_since"] in recent})
        elif _int(record.get("fails")) >= WATCH_FAILS:
            items.append({**base, "kind": "page_unread", "day": str(record.get("checked", "")),
                          "error": _clean(record.get("error", "")),
                          "new": _int(record.get("fails")) == WATCH_FAILS and record.get("checked") in recent})
        if record.get("opened") and record.get("ref_key") == target["key"]:
            items.append({**base, "kind": "page_changed", "day": str(record.get("changed") or record["opened"]),
                          "since": str(record["opened"]), "new": record.get("changed") in recent,
                          "added": [_clean(x) for x in _list(record.get("added"))[:MAX_PAGE_LINES]],
                          "removed": [_clean(x) for x in _list(record.get("removed"))[:MAX_PAGE_LINES]]})
        conds = record.get("conds") if isinstance(record.get("conds"), dict) else {}
        for kind in ("appears", "disappears"):
            for pattern in target[kind]:
                cond = conds.get(f"{kind}:{pattern}")
                if isinstance(cond, dict) and cond.get("key") == target["key"]:
                    items.append({**base, "kind": "page_" + kind, "day": str(cond.get("since", "")),
                                  "new": cond.get("since") in recent, "first": cond.get("first") is True,
                                  "pattern": _clean(pattern)})
    return items


_DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _list(value: object) -> list:
    return value if isinstance(value, list) else []


def _age(day: str, today: date) -> int:
    try:
        return (today - date.fromisoformat(day)).days
    except ValueError:
        return 0


def stale_items(dynamic: list, today: date, days: int) -> list[dict]:
    """The dynamic facts whose "updated" day is more than `days` days old (0 = not checked)."""
    items = []
    for fact in dynamic if days > 0 else []:
        try:
            updated = date.fromisoformat(str(fact["updated"]))
        except (KeyError, TypeError, ValueError):
            continue   # a fact without the day it was written: nothing to say about it
        age = (today - updated).days
        if age > days:
            items.append({"kind": "stale", "titles": [_title(fact)], "url": str(fact.get("source", "")),
                          "id": _clean(fact.get("id", "")), "day": updated.isoformat(), "age": age,
                          "new": age == days + 1, "date": None, "left": 0})
    return items


def sort_items(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda i: (KINDS.index(i["kind"]), i.get("date") or date.min, i.get("day", ""),
                                        i.get("title") or (i.get("titles") or [""])[0]))


def is_milestone(item: dict, days: int) -> bool:
    """Whether today something happens to the item that is worth a mail by itself."""
    if item["kind"] == "bad_date":
        return True
    if "new" in item:                             # a page, or a dynamic fact: told the day it is found
        return item["new"]
    if item["field"] == "expires":
        return item["left"] in (days, 1, -1)      # enters the list, tomorrow is the last day, out since today
    return item["left"] in (days, 0)              # enters the list, the check is due today


def should_send(items: list[dict], today: date, days: int) -> bool:
    if any(is_milestone(i, days) for i in items):
        return True
    # Monday: what is still open comes back until the file is edited
    return today.weekday() == 0 and any(i["kind"] in OPEN_KINDS for i in items)


def _when(item: dict) -> str:
    left, day = item["left"], f"{item['date']:%d/%m/%Y}"
    if item["kind"] == "expired":
        return f"scaduto il {day} ({'ieri' if left == -1 else f'da {-left} giorni'})"
    if item["kind"] == "expiring":
        return f"ultimo giorno: {day} ({'oggi' if left == 0 else 'domani' if left == 1 else f'tra {left} giorni'})"
    if item["kind"] == "review_overdue":
        return f"era da ricontrollare entro il {day} ({'ieri' if left == -1 else f'da {-left} giorni'})"
    return f"da ricontrollare entro il {day} ({'oggi' if left == 0 else 'domani' if left == 1 else f'tra {left} giorni'})"


def _day(text: str) -> str:
    try:
        return f"{date.fromisoformat(text):%d/%m/%Y}"
    except ValueError:
        return text


def _page_lines(item: dict) -> list[str]:
    """The lines of the mail for a page (or a dynamic fact): what happened, when, to which facts."""
    kind = item["kind"]
    if kind == "watch_stopped":
        return [f"- ultima lettura delle pagine: {_day(item['day'])} ({item['age']} giorni fa)"]
    if kind == "stale":
        lines = [f"- «{item['titles'][0]}»",
                 f"  scritta l'ultima volta il {_day(item['day'])} ({item['age']} giorni fa); job: {item['id'] or '?'}"]
        return lines + ([f"  fonte: {item['url']}"] if item["url"] else [])
    what = {"page_gone": f"non esiste più (dal {_day(item['day'])})",
            "page_unread": f"non si legge da {WATCH_FAILS} o più controlli ({item.get('error') or 'errore'})",
            "page_changed": (f"cambiata il {_day(item['day'])}" if item.get("since") in (None, item["day"]) else
                             f"cambiata dal {_day(item['since'])}, l'ultima volta il {_day(item['day'])}"),
            "page_appears": f"dal {_day(item['day'])} contiene «{item.get('pattern', '')}»",
            "page_disappears": f"dal {_day(item['day'])} non contiene più «{item.get('pattern', '')}»"}[kind]
    if item.get("first"):
        what += " (era già così alla prima lettura)"
    lines = [f"- {item['url']}", f"  {what}"]
    lines += [f"  + {x}" for x in item.get("added", [])] + [f"  − {x}" for x in item.get("removed", [])]
    return lines + [f"  fatto: «{t}»" for t in item["titles"]]


def build_report(items: list[dict], today: date, site: str, days: int) -> tuple[str, str]:
    """(subject, body) of the mail: the items by kind, each with its date and what to do."""
    lines = [(f"Fatti noti di Zautte da rivedere, al {today:%d/%m/%Y}: {len(items)} voci (date vicine o passate, "
              "pagine di origine cambiate o sparite, schede automatiche ferme)."), ""]
    for kind in KINDS:
        group = [i for i in items if i["kind"] == kind]
        if not group:
            continue
        heading = _HEADINGS[kind] + (f" (entro {days} giorni)" if kind in ("expiring", "review_soon") else "")
        lines.append(f"== {heading}: {len(group)}")
        for i in group:
            if "titles" in i:
                lines += _page_lines(i)
                continue
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
                 "ricontrollarlo. Le pagine di origine (\"source\") vengono rilette ogni mattina. Questo messaggio "
                 "arriva quando succede qualcosa, e ogni lunedì finché resta qualcosa in sospeso.")
    urgent = sum(1 for i in items if i["kind"] in URGENT_KINDS)
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
    try:   # written by the sync account from pages of the web: read as data, bounded, never trusted
        items += watch_items(entries, read_json_bounded(KNOWN_FACTS_WATCH_FILE, _WATCH_STATE_MAX_BYTES, dict), today)
    except FileNotFoundError:
        log.info("no %s yet: the pages are not in this report", KNOWN_FACTS_WATCH_FILE.name)
    except (OSError, ValueError) as exc:
        log.warning("%s cannot be read (%s): the pages are not in this report", KNOWN_FACTS_WATCH_FILE.name, exc)
    try:
        dynamic = read_json_bounded(KNOWN_FACTS_DYNAMIC_FILE, _DYNAMIC_MAX_BYTES, list)
        items += stale_items([f for f in dynamic if isinstance(f, dict)], today, KNOWN_FACTS_STALE_DAYS)
    except (OSError, ValueError):
        pass   # no dynamic facts, or unreadable: the API logs that
    items = sort_items(items)
    dated = sum(1 for e in entries if any(e.get(f) for f in DATE_FIELDS))
    if not items:
        log.info("%d facts, %d with a date: nothing near, past, changed or stale; nothing sent", len(entries), dated)
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
