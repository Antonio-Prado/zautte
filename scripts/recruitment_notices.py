"""
Calls for jobs («Bandi di concorso») open today, kept up to date by cron.

"Are there any open competitions? I would like to work for the municipality" needs today's date
and the period in which each call accepts applications. The transparency job indexes every call
as a passage (its attachments too), but a search by similarity finds the calls that look like
the question, not the ones open today. This job reads the search table of the «Bandi di
concorso» section of the transparency portal (the same request the page makes, see
scripts/amt_sync.py), sorts the calls by their validity dates, and writes them as one known
fact in KNOWN_FACTS_DYNAMIC_FILE (same format as config/known_facts.json): the calls open today
with their deadline and page (and the links of their detail page, such as the national
recruitment portal), the ones announced for later, the ones closed in the last `closed_days`
days, so the model does not present a closed call as open. The API rereads that file when it
changes: no restart.

The dates of the table are what the office typed in two fields («Inizio validità», «Fine
validità»): often the period for applications, sometimes how long the page stays published. The
deadline and the requirements are the ones written in the notice, so the job also reads the
attachments of the open and announced calls and quotes in the fact what the notice says about
both; when the notice counts the days from its publication, the date is worked out. The text of
the notice comes before the dates of the table. What was read is kept by the fingerprint of the
attachment (NOTICE_CACHE_FILE): an attachment is downloaded once.

Configuration: the "recruitment" key of config/crawl_extra.json (portal and page_id come from "amt")
    {"voceamt": 544,                   # the section with the table of the calls
     "closed_days": 90,                # recently closed calls listed, to say they are closed (default)
     "keywords": [...], "require_any": [...]}   # optional: when the fact is injected
Run (hourly from cron, as the sync account, through scripts/recruitment_notices.sh: lock, memory ceiling, time limit):
    venv/bin/python -m scripts.recruitment_notices [--dry-run]
Exit codes: 0 written or unchanged, 1 fetch or configuration error, 2 no table on the page
(in both cases the previous file is kept).
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.privdrop import drop_to_sync_user

drop_to_sync_user()   # as root: become the sync account before config.settings reads .env

import httpx

from config.settings import (
    AMT_SOURCES,
    CRAWL_DELAY_SECONDS,
    KNOWN_FACTS_DYNAMIC_FILE,
    RECRUITMENT_NOTICES,
    SITE_NAME,
    now_local,
)
from scripts.amt_sync import _HEADERS, _get, _iso, _pdf_text, page_url, parse_page, table_rows
from scripts.council_sessions import MONTHS, upsert_fact

log = logging.getLogger("recruitment_notices")

FACT_ID = "recruitment-open"
DETAIL_PAGES = 30          # detail pages read per run (the open and announced calls): their links
_FROM_RE = re.compile(r"inizio\s+validit\S*:\s*([^;]+)", re.IGNORECASE)
_TO_RE = re.compile(r"fine\s+validit\S*:\s*([^;]+)", re.IGNORECASE)
_URL_RE = re.compile(r"https?://[^\s<>\"']+")
_PERCENT_U_RE = re.compile(r"%u([0-9a-fA-F]{4})")   # «%u201C»: a quote the portal did not decode

# The text of the notices (the attachments of a call)
NOTICE_CACHE_FILE = KNOWN_FACTS_DYNAMIC_FILE.parent / "recruitment_notices_cache.json"
NOTICE_ATTACHMENTS = 3       # attachments of a call read looking for the notice (it is usually the first)
NOTICE_MAX_CHARS = 120_000   # text read from one attachment
DEADLINE_CHARS = 450         # of the passage about the deadline quoted in the fact
REQUIREMENTS_CHARS = 700     # of the passage about the requirements
# Where a notice states the deadline for applications, most specific wording first
_DEADLINE_RES = [re.compile(p, re.IGNORECASE) for p in (
    r"termine\s+(?:perentorio\s+)?(?:di|per\s+la)\s+presentazione\s+dell[ae]\s+(?:domand[ae]|istanz[ae]|candidatur[ae])",
    r"scadenza\s+(?:del\s+termine\s+)?(?:di|per\s+la)\s+presentazione",
    r"entro\s+e\s+non\s+oltre",
    r"termine\s+perentorio")]
# The heading of the requirements (in capitals in the notices), then any mention of them
_REQUIREMENTS_RES = [re.compile(r"REQUISITI\s+(?:[A-Z]+\s+){0,2}(?:DI|PER)\s+(?:L\s*['’]\s*)?AMMISSIONE"),
                     re.compile(r"requisiti\s+(?:generali\s+|generici\s+|specifici\s+)?(?:di|per)\s+"
                                r"(?:l\s*['’]\s*)?(?:ammissione|partecipazione)", re.IGNORECASE)]
_PAGE_MARK_RE = re.compile(r"\[Pagina \d+\]")
# «ore 23:59 del 30° giorno successivo alla pubblicazione»: a deadline counted from the publication
_DAYS_AFTER_RE = re.compile(r"(\d{1,3})\s*[°º^]?\s*giorno\s+successivo\s+(?:alla\s+data\s+di\s+|all[ao']\s*)"
                            r"(?:sua\s+)?pubblicazione", re.IGNORECASE)

# Words of a question about working for the administration. A word in both lists is enough; the
# weak ones (only in keywords: «bando», «lavoro», «posto», «personale») need a strong one.
_STRONG = [
    "concorso", "concorsi", "concorsuale", "concorsuali", "selezione", "selezioni", "assunzione", "assunzioni",
    "assumere", "assunto", "assunta", "lavorare", "impiego", "impieghi", "mobilità", "mobilita",
    "graduatoria", "graduatorie", "reclutamento", "candidatura", "candidature", "candidarmi", "candidarsi", "inpa",
]
DEFAULT_KEYWORDS = sorted(set(_STRONG + ["bando", "bandi", "lavoro", "posto", "posti", "personale", "dipendente",
                                         "dipendenti", "comune", "comunale", "comunali"]))
DEFAULT_REQUIRE_ANY = sorted(set(_STRONG))


def _day(d: date) -> str:
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def _date(text: str) -> date | None:
    iso = _iso(text or "")
    return date.fromisoformat(iso) if iso else None


def classify(rows: list[dict], today: date, closed_days: int = 90) -> dict[str, list[dict]]:
    """The calls by their validity dates: open today, announced for later, closed in the last
    `closed_days` days. A call without dates counts as open (the portal shows it)."""
    out: dict[str, list[dict]] = {"open": [], "upcoming": [], "closed": []}
    for row in rows:
        desc = row.get("description", "")
        start = _date((_FROM_RE.search(desc) or [None, ""])[1])
        end = _date((_TO_RE.search(desc) or [None, ""])[1])
        call = {**row, "start": start, "end": end,
                "title": _PERCENT_U_RE.sub(lambda m: chr(int(m[1], 16)), row.get("title", ""))}
        if start and start > today:
            out["upcoming"].append(call)
        elif end and end < today:
            if (today - end).days <= closed_days:
                out["closed"].append(call)
        else:
            out["open"].append(call)
    out["open"].sort(key=lambda c: (c["end"] or date.max, c["title"]))
    out["upcoming"].sort(key=lambda c: (c["start"] or date.max, c["title"]))
    out["closed"].sort(key=lambda c: (c["end"] or date.min, c["title"]), reverse=True)
    return out


def detail(fetch, call: dict) -> dict:
    """What the detail page of a call adds: its full title (the table cuts the long ones) and the
    links of the page (the national recruitment portal, the page of the selection), in the order
    they appear; {} when the page cannot be read."""
    try:
        page = parse_page(fetch(call["detail"]), call["detail"])
    except (httpx.HTTPError, ValueError) as e:
        log.warning("%s: detail not read (%s)", call["detail"], e)
        return {}
    if not page["docs"]:
        return {}
    doc = page["docs"][0]
    links: list[str] = []
    for url in _URL_RE.findall(doc.get("description", "")):
        url = url.rstrip(".,;)")
        if url not in links and "event=vediAllegato" not in url:
            links.append(url)
    out = {"links": links, "attachments": doc.get("attachments", [])}
    if doc.get("title") and len(doc["title"]) >= len(call.get("title", "").rstrip(". ")):
        out["title"] = doc["title"]
    return out


def _passage(text: str, start: int, before: int, length: int) -> str:
    """The text around `start` on one line: from the sentence that contains it (at most `before`
    characters back) for `length` characters, cut at the end of a word."""
    begin = max(0, start - before)
    dot = max(text.rfind(". ", begin, start), text.rfind(": ", begin, start), text.rfind("; ", begin, start))
    begin = dot + 2 if dot >= 0 else begin
    passage = " ".join(text[begin:begin + length].split())
    if begin + length < len(text) and " " in passage:
        passage = passage.rsplit(" ", 1)[0] + " […]"
    return passage


def notice_passages(text: str) -> dict:
    """What a notice says about the deadline for applications and about who can apply: the two
    passages as they are written, {} for a text that speaks of neither (a form, a list)."""
    # one line, without the page marks of the PDF reader: a sentence is not cut where the page ends
    text = " ".join(_PAGE_MARK_RE.sub(" ", text or "").split())
    out = {}
    for pattern in _DEADLINE_RES:
        if m := pattern.search(text):
            out["deadline"] = _passage(text, m.start(), 250, DEADLINE_CHARS)
            break
    for pattern in _REQUIREMENTS_RES:
        if m := pattern.search(text):
            out["requirements"] = " ".join(text[m.start():m.start() + REQUIREMENTS_CHARS].split())
            if m.start() + REQUIREMENTS_CHARS < len(text):
                out["requirements"] = out["requirements"].rsplit(" ", 1)[0] + " […]"
            break
    return out


def load_cache(path: Path = NOTICE_CACHE_FILE) -> dict:
    try:
        cache = json.loads(path.read_text(encoding="utf-8"))
        return cache if isinstance(cache, dict) else {}
    except (OSError, ValueError):
        return {}


def save_cache(cache: dict, path: Path = NOTICE_CACHE_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read_notice(get_bytes, call: dict, cache: dict, pdf_text=_pdf_text) -> dict:
    """The passages of the notice of a call: its attachments are read in order (at most
    NOTICE_ATTACHMENTS) until one speaks of the deadline. Each attachment is read once: what it
    says is kept in `cache` by its fingerprint, also when it says nothing. {} when no attachment
    could be read or none is a notice."""
    found: dict = {}
    for att in call.get("attachments", [])[:NOTICE_ATTACHMENTS]:
        key = att.get("md5") or ""
        if key and key in cache:
            passages = cache[key]
        else:
            try:
                passages = notice_passages(pdf_text(get_bytes(att["url"]), NOTICE_MAX_CHARS))
            except Exception as e:  # noqa: BLE001 - a download or a PDF that fails: the call is listed without its text
                log.warning("%s: attachment «%s» not read (%s)", call.get("detail"), att.get("name"), e)
                continue
            if key:
                cache[key] = passages
        if passages and not found:
            found = {**passages, "notice": att.get("name", "")}
        if found.get("deadline"):
            break
    return found


def notice_deadline(call: dict) -> date | None:
    """The last day for applications when the notice counts it from the publication («30° giorno
    successivo alla pubblicazione») and the table says when the call was published."""
    m = _DAYS_AFTER_RE.search(call.get("deadline") or "")
    if m and call.get("start"):
        return call["start"] + timedelta(days=int(m[1]))
    return None


def _period(call: dict) -> str:
    """The validity the table gives: as the office typed it, not necessarily the period for applications."""
    if call["start"] and call["end"]:
        return f"validità indicata nella tabella: dal {call['start']:%d/%m/%Y} al {call['end']:%d/%m/%Y}"
    if call["end"]:
        return f"validità indicata nella tabella: fino al {call['end']:%d/%m/%Y}"
    if call["start"]:
        return f"validità indicata nella tabella: dal {call['start']:%d/%m/%Y}, fine non indicata"
    return "validità non indicata nella tabella"


def _notice_lines(call: dict, today: date) -> list[str]:
    """What the page and the notice of a call say, under its line in the fact."""
    lines = []
    if call.get("attachments"):
        lines.append("  Allegati pubblicati sulla pagina: " + "; ".join(a["name"] for a in call["attachments"]) + ".")
    if call.get("deadline"):
        line = f"  Termine per le domande, dal testo dell'avviso («{call['notice']}»): «{call['deadline']}»"
        last = notice_deadline(call)
        if last:
            days = int(_DAYS_AFTER_RE.search(call["deadline"])[1])
            line += (f" Data calcolata: pubblicazione il {call['start']:%d/%m/%Y} (inizio validità della tabella) "
                     f"più {days} giorni = {last:%d/%m/%Y}: "
                     + ("termine GIÀ PASSATO, le domande non si possono più presentare."
                        if last < today else "termine non ancora passato."))
        lines.append(line)
    elif call.get("attachments"):
        lines.append("  Il termine per le domande non è stato letto in automatico dagli allegati: è scritto "
                     "nell'avviso pubblicato sulla pagina del bando.")
    if call.get("requirements"):
        lines.append(f"  Chi può partecipare, dal testo dell'avviso: «{call['requirements']}»")
    return lines


def build_fact(calls: dict[str, list[dict]], today: date, section_url: str, site_name: str,
               closed_days: int = 90, keywords: list[str] | None = None,
               require_any: list[str] | None = None) -> dict:
    """The known fact: the calls open today, the ones announced, the ones just closed, and how to answer."""
    lines = [(f"Bandi di concorso, selezioni e avvisi di mobilità per lavorare al {site_name}: elenco letto "
              f"automaticamente il {_day(today)} dalla sezione «Bandi di concorso» dell'Amministrazione Trasparente "
              f"({section_url}). La tabella indica per ogni bando un periodo di validità (inizio e fine), che "
              "non sempre è il periodo in cui si accettano le domande: il termine per le domande e i requisiti sono "
              "quelli scritti nell'avviso allegato a ogni bando, riportati qui sotto con le parole dell'avviso."), ""]
    lines.append("BANDI CON LA VALIDITÀ IN CORSO OGGI NELLA TABELLA:")
    if calls["open"]:
        for c in calls["open"]:
            lines.append(f"- {c['title']} ({_period(c)}). Pagina del bando, con gli allegati e le modalità di "
                         f"domanda: {c['detail']}" + (f". Collegamenti: {', '.join(c['links'])}" if c.get("links") else ""))
            lines.extend(_notice_lines(c, today))
    else:
        lines.append(f"- Nessuno: al {today:%d/%m/%Y} non c'è alcun bando di concorso o selezione con la validità in corso.")
    lines.append("")
    if calls["upcoming"]:
        lines.append("BANDI ANNUNCIATI (la validità comincia più avanti):")
        for c in calls["upcoming"]:
            lines.append(f"- {c['title']} ({_period(c)}). Pagina: {c['detail']}"
                         + (f". Collegamenti: {', '.join(c['links'])}" if c.get("links") else ""))
            lines.extend(_notice_lines(c, today))
        lines.append("")
    if calls["closed"]:
        lines.append(f"BANDI CON LA VALIDITÀ FINITA NEGLI ULTIMI {closed_days} GIORNI (le domande non si accettano più; "
                     "sulla pagina possono comparire esiti e graduatorie):")
        for c in calls["closed"]:
            lines.append(f"- {c['title']} (validità finita il {c['end']:%d/%m/%Y}). Pagina: {c['detail']}")
        lines.append("")
    lines.append("Come rispondere: a chi chiede se ci sono concorsi aperti o come fare a lavorare al Comune, elenca i "
                  "bandi con la validità in corso e per ognuno di' il termine per le domande e chi può partecipare con "
                  "quello che scrive l'avviso, e il link alla pagina. Il testo dell'avviso viene prima delle date della "
                  "tabella: se per l'avviso il termine è già passato, dillo subito e non presentare il bando come "
                  "aperto, anche se la validità della tabella non è finita (aggiungi che sulla pagina il bando risulta "
                  "ancora pubblicato e, se ci sono, quali altri allegati sono usciti, per esempio ammessi o date delle "
                  "prove). Quando l'avviso conta i giorni dalla pubblicazione, riporta la data calcolata qui sopra. "
                  "Se il termine dell'avviso non è riportato, da' le date della tabella dicendo che sono la validità "
                  "indicata sul sito e che il termine esatto è nell'avviso. Quando l'avviso riserva la procedura a "
                  "certe persone (per esempio a chi è già dipendente di una pubblica amministrazione), dillo subito "
                  "accanto al bando. Non aggiungere requisiti o scadenze che l'avviso non scrive. Se non ci sono "
                  "bandi, dillo chiaramente e indica la sezione «Bandi di concorso», dove i nuovi bandi vengono "
                  "pubblicati, e il portale nazionale del reclutamento inPA quando il bando lo prevede. Le domande si "
                  "presentano nei modi scritti in ogni bando, non tramite questo assistente. Non citare bandi che non "
                  "sono in questo elenco.")
    lines.append(f"Tutti i bandi, anche quelli conclusi, con esiti e graduatorie: {section_url}")
    return {
        "id": FACT_ID,
        "keywords": keywords or DEFAULT_KEYWORDS,
        "require_any": require_any or DEFAULT_REQUIRE_ANY,
        "title": f"Bandi di concorso e selezioni aperti oggi per lavorare al Comune (aggiornato automaticamente) - {site_name}",
        "source": section_url,
        "category": "servizio",
        "updated": today.isoformat(),
        "text": "\n".join(lines).rstrip() + "\n",
    }


def read_calls(fetch, post, config: dict, voceamt: int) -> list[dict] | None:
    """The rows of the table of the calls; None when the page has no table."""
    url = page_url(config["portal"], int(config["page_id"]), voceamt)
    page = parse_page(fetch(url), url)
    if page["table"] is None:
        return None
    return table_rows(post, config, voceamt, page["table"], url)


def main() -> int:
    ap = argparse.ArgumentParser(description="Calls for jobs open today (transparency portal) → dynamic known fact")
    ap.add_argument("--dry-run", action="store_true", help="print the fact, write nothing")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    voceamt = int(RECRUITMENT_NOTICES.get("voceamt") or 0)
    if not voceamt or not AMT_SOURCES.get("portal") or not AMT_SOURCES.get("page_id"):
        log.error("no recruitment.voceamt (or amt.portal/page_id) in config/crawl_extra.json: nothing to do")
        return 1
    config = {**AMT_SOURCES, "portal": AMT_SOURCES["portal"].rstrip("/") + "/"}
    closed_days = int(RECRUITMENT_NOTICES.get("closed_days") or 90)
    today = now_local().date()
    section_url = page_url(config["portal"], int(config["page_id"]), voceamt)
    try:
        with httpx.Client(headers=_HEADERS, follow_redirects=False, timeout=90.0, trust_env=False) as client:
            def fetch(url):
                return _get(client, url).text

            def get_bytes(url):
                return _get(client, url).content

            def post(url, form):
                resp = client.post(url, data=form)
                time.sleep(CRAWL_DELAY_SECONDS)
                resp.raise_for_status()
                return resp.json()

            rows = read_calls(fetch, post, config, voceamt)
            if rows is None:
                log.error("%s: no search table on the page", section_url)
                return 2
            calls = classify(rows, today, closed_days)
            cache = load_cache()
            known = dict(cache)
            for c in (calls["open"] + calls["upcoming"])[:DETAIL_PAGES]:
                c.update(detail(fetch, c))
                c.update(read_notice(get_bytes, c, cache))
            if cache != known and not args.dry_run:
                save_cache(cache)
    except Exception as exc:  # noqa: BLE001 - any network/policy/format error: keep the previous file
        log.error("could not read %s: %s", section_url, exc)
        return 1
    fact = build_fact(calls, today, section_url, SITE_NAME, closed_days,
                      RECRUITMENT_NOTICES.get("keywords"), RECRUITMENT_NOTICES.get("require_any"))
    if args.dry_run:
        print(fact["text"])
    written = upsert_fact(KNOWN_FACTS_DYNAMIC_FILE, fact, dry_run=args.dry_run)
    log.info("%d calls: %d open, %d announced, %d closed in the last %d days (%d characters); %s",
             len(rows), len(calls["open"]), len(calls["upcoming"]), len(calls["closed"]), closed_days,
             len(fact["text"]), "written" if written and not args.dry_run else "unchanged" if not written else "dry run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
