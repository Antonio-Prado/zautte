"""
Tenders with offers still open today, kept up to date by cron.

"Are there any tenders open now?" needs today's date and the deadline of each procedure. The
transparency job indexes every tender document as a passage, but a search by similarity finds
the tenders that look like the question, not the ones open today: the answer named a tender
closed seven months before and "found none with a later deadline" while two were open. This
job reads the pages of the «Bandi di gara e contratti» section of the transparency portal that
list the tender documents (see scripts/amt_sync.py), takes the deadline each block states
(«Scadenza presentazione offerte: 03/12/2026 ore 11:00») and writes one known fact in
KNOWN_FACTS_DYNAMIC_FILE (same format as config/known_facts.json): the procedures still open
today with deadline, page and procurement platform, the recent ones that state no deadline, the
ones closed in the last `closed_days` days, and how to answer. The API rereads that file when
it changes: no restart.

Configuration: the "tenders" key of config/crawl_extra.json (portal and page_id come from "amt")
    {"voceamt": [561],                 # the pages that list the tender documents
     "closed_days": 60,                # recently closed procedures listed, to say they are closed (default)
     "undated_days": 60,               # procedures without a stated deadline listed if published in the last N days
     "keywords": [...], "require_any": [...]}   # optional: when the fact is injected
Run (hourly from cron, as the sync account, through scripts/tender_notices.sh):
    venv/bin/python -m scripts.tender_notices [--dry-run]
Exit codes: 0 written or unchanged, 1 fetch or configuration error, 2 no document on the pages
(in both cases the previous file is kept).
"""

import argparse
import logging
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.privdrop import drop_to_sync_user

drop_to_sync_user()   # as root: become the sync account before config.settings reads .env

import httpx

from config.settings import (
    AMT_SOURCES,
    KNOWN_FACTS_DYNAMIC_FILE,
    SITE_NAME,
    TENDER_NOTICES,
    now_local,
)
from scripts.amt_sync import _HEADERS, _get, page_url, parse_page, slug
from scripts.council_sessions import MONTHS, upsert_fact

log = logging.getLogger("tender_notices")

FACT_ID = "tenders-open"
CLOSED_DAYS = 60
UNDATED_DAYS = 60
MAX_LISTED = 12            # closed and undated procedures listed at most, each
MAX_TITLE = 260
# «Scadenza presentazione offerte: 03/12/2026 ore 11:00», «Scadenza 03/11/2026 ore 11 :00», «SCADENZA 03/07/2026 ORE 12»
_DEADLINE_RE = re.compile(r"scadenza[^0-9]{0,90}?(\d{1,2})[/.](\d{1,2})[/.](20\d{2})"
                          r"(?:\s*(?:ore|alle(?:\s+ore)?)\s*(\d{1,2})(?:\s*[:.]\s*(\d{2}))?)?", re.IGNORECASE)
_URL_RE = re.compile(r"https?://[^\s<>\"']+")

DEFAULT_KEYWORDS = sorted({"gara", "gare", "appalto", "appalti", "affidamento", "affidamenti", "tender", "tenders",
                           "procurement"})


def _day(d: date) -> str:
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def deadline(text: str) -> tuple[date, str] | None:
    """(day, «ore 11:00» or '') of the deadline a tender block states, or None."""
    m = _DEADLINE_RE.search(text or "")
    if not m:
        return None
    try:
        day = date(int(m[3]), int(m[2]), int(m[1]))
    except ValueError:
        return None
    hour = f"ore {int(m[4])}:{m[5] or '00'}" if m[4] and int(m[4]) <= 24 else ""
    return day, hour


def read_tenders(fetch, portal: str, page_id: int, pages: list[int]) -> list[dict]:
    """The tender documents of the pages: title, description, publication date, the address of the
    document (page + fragment, as scripts/amt_sync.py indexes it), its deadline and its links."""
    out: list[dict] = []
    for voce in pages:
        url = page_url(portal, page_id, voce)
        page = parse_page(fetch(url), url)
        used: dict[str, int] = {}
        for doc in page["docs"]:
            s = slug(doc["title"])
            used[s] = used.get(s, 0) + 1
            if used[s] > 1:
                s = f"{s}-{used[s]}"
            try:
                published = date.fromisoformat(str(doc.get("published", ""))[:10])
            except ValueError:
                published = None
            links: list[str] = []
            for link in _URL_RE.findall(doc.get("description", "")):
                link = link.rstrip(".,;)")
                if link not in links:
                    links.append(link)
            out.append({"title": doc["title"], "source": f"{url}#{s}", "published": published,
                        "deadline": deadline(doc.get("description", "")), "links": links})
    return out


def classify(tenders: list[dict], today: date, closed_days: int = CLOSED_DAYS,
             undated_days: int = UNDATED_DAYS) -> dict[str, list[dict]]:
    """Open today (the deadline is today or later), without a stated deadline (if recent), closed in
    the last `closed_days` days. The day of the deadline counts as open: the hour is in the entry."""
    out: dict[str, list[dict]] = {"open": [], "undated": [], "closed": []}
    for t in tenders:
        if t["deadline"] is None:
            if t["published"] and (today - t["published"]).days <= undated_days:
                out["undated"].append(t)
        elif t["deadline"][0] >= today:
            out["open"].append(t)
        elif (today - t["deadline"][0]).days <= closed_days:
            out["closed"].append(t)
    out["open"].sort(key=lambda t: (t["deadline"][0], t["title"]))
    out["undated"].sort(key=lambda t: (t["published"], t["title"]), reverse=True)
    out["closed"].sort(key=lambda t: (t["deadline"][0], t["title"]), reverse=True)
    return out


def _title(t: dict) -> str:
    title = " ".join(t["title"].split())
    return title if len(title) <= MAX_TITLE else title[:MAX_TITLE].rsplit(" ", 1)[0] + "…"


def _entry(t: dict) -> str:
    parts = []
    if t["deadline"]:
        day, hour = t["deadline"]
        parts.append(f"scadenza: {day:%d/%m/%Y}" + (f" {hour}" if hour else ""))
    if t["published"]:
        parts.append(f"pubblicata il {t['published']:%d/%m/%Y}")
    line = f"- {_title(t)} ({'; '.join(parts)}). Documenti di gara: {t['source']}"
    return line + (f". Piattaforma: {t['links'][0]}" if t["links"] else "")


def build_fact(tenders: dict[str, list[dict]], today: date, section_url: str, site_name: str,
               closed_days: int = CLOSED_DAYS, keywords: list[str] | None = None,
               require_any: list[str] | None = None) -> dict:
    """The known fact: the procedures open today, the recent ones without a deadline, the ones just closed."""
    lines = [(f"Gare d'appalto, concessioni e avvisi per gli operatori economici del {site_name}: elenco letto "
              f"automaticamente il {_day(today)} dalla sezione «Bandi di gara e contratti» dell'Amministrazione "
              f"Trasparente ({section_url}), con la scadenza scritta in ogni scheda."), ""]
    lines.append("PROCEDURE ANCORA APERTE OGGI (offerte o manifestazioni di interesse non ancora scadute):")
    if tenders["open"]:
        lines += [_entry(t) for t in tenders["open"]]
    else:
        lines.append(f"- Nessuna: al {today:%d/%m/%Y} nessuna scheda pubblicata ha una scadenza ancora da venire.")
    lines.append("")
    if tenders["undated"]:
        lines.append("PROCEDURE PUBBLICATE DI RECENTE CHE NON INDICANO LA SCADENZA NELLA SCHEDA (va letta nei documenti di gara):")
        lines += [_entry(t) for t in tenders["undated"][:MAX_LISTED]]
        lines.append("")
    if tenders["closed"]:
        lines.append(f"PROCEDURE SCADUTE NEGLI ULTIMI {closed_days} GIORNI (non si possono più presentare offerte):")
        lines += [_entry(t) for t in tenders["closed"][:MAX_LISTED]]
        lines.append("")
    lines.append("Come rispondere: a chi chiede se ci sono gare o appalti aperti, elenca le procedure ancora aperte oggi con "
                  "la scadenza e il link ai documenti di gara; se non ce ne sono, dillo e indica la sezione. Le offerte si "
                  "presentano sulla piattaforma telematica indicata nei documenti, non tramite questo assistente. Non "
                  "presentare come aperta una procedura scaduta e non citare procedure che non sono in questo elenco. "
                  "Importi, requisiti e criteri sono nei documenti di gara: non riassumerli da qui.")
    lines.append(f"Tutte le procedure, anche quelle concluse: {section_url}")
    return {
        "id": FACT_ID,
        "keywords": keywords or DEFAULT_KEYWORDS,
        "require_any": require_any or keywords or DEFAULT_KEYWORDS,
        "title": f"Gare d'appalto e avvisi ancora aperti oggi (aggiornato automaticamente) - {site_name}",
        "source": section_url,
        "category": "documento",
        "updated": today.isoformat(),
        "text": "\n".join(lines).rstrip() + "\n",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Tenders open today (transparency portal) → dynamic known fact")
    ap.add_argument("--dry-run", action="store_true", help="print the fact, write nothing")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    pages = TENDER_NOTICES.get("voceamt") or []
    pages = [int(p) for p in (pages if isinstance(pages, list) else [pages])]
    if not pages or not AMT_SOURCES.get("portal") or not AMT_SOURCES.get("page_id"):
        log.error("no tenders.voceamt (or amt.portal/page_id) in config/crawl_extra.json: nothing to do")
        return 1
    portal, page_id = AMT_SOURCES["portal"].rstrip("/") + "/", int(AMT_SOURCES["page_id"])
    closed_days = int(TENDER_NOTICES.get("closed_days") or CLOSED_DAYS)
    today = now_local().date()
    section_url = page_url(portal, page_id, pages[0])
    try:
        with httpx.Client(headers=_HEADERS, follow_redirects=False, timeout=90.0, trust_env=False) as client:
            found = read_tenders(lambda url: _get(client, url).text, portal, page_id, pages)
    except Exception as exc:  # noqa: BLE001 - any network/policy/format error: keep the previous file
        log.error("could not read %s: %s", section_url, exc)
        return 1
    if not found:
        log.error("%s: no tender document on the page", section_url)
        return 2
    tenders = classify(found, today, closed_days, int(TENDER_NOTICES.get("undated_days") or UNDATED_DAYS))
    fact = build_fact(tenders, today, section_url, SITE_NAME, closed_days,
                      TENDER_NOTICES.get("keywords"), TENDER_NOTICES.get("require_any"))
    if args.dry_run:
        print(fact["text"])
    written = upsert_fact(KNOWN_FACTS_DYNAMIC_FILE, fact, dry_run=args.dry_run)
    log.info("%d tender documents: %d open, %d without deadline, %d closed in the last %d days (%d characters); %s",
             len(found), len(tenders["open"]), len(tenders["undated"]), len(tenders["closed"]), closed_days,
             len(fact["text"]), "written" if written and not args.dry_run else "unchanged" if not written else "dry run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
