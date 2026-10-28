"""
Registers of acts (resolutions of the council and of the executive, and the like) published on
a Nuvola portal (PalItalsoft, the «itaFrontOffice» search pages used by many municipalities).

The portal has no page per act that a crawler could follow: each register is a search form
(number, year, subject...) and the link to an act carries a token that expires. Its search by
subject only finds the exact word of the title, so «eventi» does not find a resolution called
«Iniziative culturali autunno 2026». This job reads, for every register and for the last
`years` years, the full index of the year (number, date, subject, publication date: one request
for the form and its token, one for the results), and indexes every act as a passage of its
own, so the assistant finds the acts about a subject in any wording and can give their number.

Every passage carries origin=acts: the weekly and monthly syncs do not remove them as «stale»,
and this job replaces or removes them when the portal changes (an act no longer in the index of
a year read correctly is removed). A run with nothing new does not load the vector store. It
also writes the dynamic known fact «acts-latest» with the last acts of each register, for the
questions about the most recent ones.

Configuration: the "acts" key of config/crawl_extra.json
    {"portal": "https://example.nuvolapalitalsoft.it/",
     "years": 2,                         # current year and the previous ones (default 2)
     "latest": 10,                       # acts per register in the «latest acts» fact (default 10)
     "section": "atti",                  # section label of the passages (default "atti")
     "registers": [{"page_id": 35564, "name": "Delibera di Giunta comunale",
                    "plural": "delibere di Giunta comunale"}]}
Run (from cron through scripts/acts_sync.sh, which takes the lock of the syncs):
    venv/bin/python -m scripts.sync acts
    venv/bin/python -m scripts.acts_sync --dry-run      # read and print, write nothing
"""
import argparse
import hashlib
import json
import logging
import re
import sys
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.privdrop import drop_to_sync_user

drop_to_sync_user()   # as root: become the sync account before config.settings reads .env

from config.settings import (
    ACTS_SOURCES,
    CRAWL_ALLOWED_DOMAINS,
    CRAWL_CACHE_DIR,
    CRAWL_DELAY_SECONDS,
    KNOWN_FACTS_DYNAMIC_FILE,
    SITE_NAME,
    now_local,
)
from crawler.netpolicy import get_limited
from indexer.chunker import chunk_document

log = logging.getLogger(__name__)

STATE_FILE = CRAWL_CACHE_DIR / "acts_state.json"
FACT_ID = "acts-latest"
MONTHS_IT = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio",
             "agosto", "settembre", "ottobre", "novembre", "dicembre"]
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; Zautte/1.0)"}
_TOKEN_RE = re.compile(r'name="pub_token"[^>]*value="([^"]+)"')
_DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")

# «Latest acts» fact: a question about acts (keywords) that asks for the latest ones (require_any)
FACT_KEYWORDS = ["delibera", "delibere", "deliberazione", "deliberazioni", "atti", "atto", "giunta", "consiglio",
                 "approvato", "approvata", "approvate", "approvati", "adottate", "pubblicate", "pubblicati"]
FACT_REQUIRE_ANY = ["ultime", "ultima", "ultimi", "ultimo", "recenti", "recente", "nuove", "nuovi",
                    "settimana", "settimane", "mese", "giorni", "ieri", "oggi", "elenco", "lista"]


# --------------------------------------------------------------------------- state

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            log.warning(f"Acts state unreadable ({e}): starting from scratch")
    return {"items": {}}


def save_state(state: dict) -> None:
    CRAWL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STATE_FILE)


# --------------------------------------------------------------------------- portal

def _date(text: str) -> str:
    """dd/mm/yyyy → yyyy-mm-dd ("" if it is not a date)."""
    m = _DATE_RE.match(text.strip())
    if not m:
        return ""
    try:
        return date(int(m[3]), int(m[2]), int(m[1])).isoformat()
    except ValueError:
        return ""


def date_it(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} {MONTHS_IT[d.month - 1]} {d.year}"


def parse_acts(html: str) -> list[dict]:
    """Rows of the results table: number, date, kind (the bold line of the subject cell),
    subject, publication date. Rows that do not look like an act are skipped."""
    acts = []
    for tr in BeautifulSoup(html, "lxml").find_all("tr"):
        tds = tr.find_all("td", recursive=False)
        if len(tds) < 5:
            continue
        number = tds[0].get_text(strip=True)
        when = _date(tds[1].get_text(strip=True))
        if not number.isdigit() or not when:
            continue
        cell = tds[2]
        kind_tag = cell.find("span")
        kind = kind_tag.get_text(" ", strip=True) if kind_tag else ""
        if kind_tag:
            kind_tag.extract()
        subject = re.sub(r"\s+", " ", cell.get_text(" ", strip=True)).strip()
        if not subject:
            continue
        acts.append({"number": int(number), "date": when, "kind": kind, "subject": subject,
                     "published": _date(tds[4].get_text(strip=True))})
    return acts


def _get(client: httpx.Client, url: str) -> str:
    resp = get_limited(client, url, CRAWL_ALLOWED_DOMAINS)
    time.sleep(CRAWL_DELAY_SECONDS)
    resp.raise_for_status()
    return resp.text


def fetch_register(client: httpx.Client, portal: str, page_id: int, year: int) -> str:
    """The index of one year: the search form (for its token), then the results without filters."""
    form = _get(client, f"{portal}?page_id={page_id}")
    m = _TOKEN_RE.search(form)
    if not m:
        raise ValueError(f"no pub_token in the search page {page_id}")
    query = urlencode({"page_id": page_id, "event": "risultati", "visualizzazione": "sintetico",
                       "daatto": "", "alatto": "", "anno": year, "mittente": "", "settore": "",
                       "oggetto": "", "ordinamento": "atto", "pub_token": m[1]})
    return _get(client, f"{portal}?{query}")


# --------------------------------------------------------------------------- passages

def act_source(portal: str, page_id: int, year: int, number: int) -> str:
    """One address per act: the search page of its register (the act itself has no stable
    link), with a fragment that tells the acts apart."""
    return f"{portal}?page_id={page_id}#atto-{year}-{number}"


def act_item(register: dict, year: int, act: dict) -> dict:
    return {"page_id": int(register["page_id"]), "year": year, "number": act["number"], "date": act["date"],
            "published": act["published"], "subject": act["subject"],
            "name": register.get("name") or act["kind"] or "Atto",
            "plural": register.get("plural") or "atti"}


def act_text(portal: str, item: dict) -> str:
    text = (f"{item['name']} n. {item['number']} del {date_it(item['date'])}. Oggetto: {item['subject']}")
    if not text.endswith("."):
        text += "."
    if item["published"]:
        text += f" Data di pubblicazione all'Albo pretorio online: {date_it(item['published'])}."
    text += (f" Il testo completo si apre dalla pagina di ricerca delle {item['plural']} sul portale degli atti "
             f"({portal}?page_id={item['page_id']}), indicando il numero {item['number']} e l'anno {item['year']}.")
    return text


def act_title(item: dict) -> str:
    subject = item["subject"] if len(item["subject"]) <= 160 else item["subject"][:157].rstrip() + "…"
    d = date.fromisoformat(item["date"])
    return f"{item['name']} n. {item['number']} del {d:%d/%m/%Y}: {subject}"


def act_doc(portal: str, source: str, item: dict) -> dict:
    chunks = chunk_document(
        text=act_text(portal, item), source_url=source, title=act_title(item), doc_type="html",
        extra_metadata={"category": "atto", "section": ACTS_SOURCES.get("section", "atti"),
                        "date": item["date"], "origin": "acts"},
    )
    return {"url": source, "chunks": chunks}


def _digest(item: dict) -> str:
    return hashlib.sha256(json.dumps(item, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


# --------------------------------------------------------------------------- run

def collect(fetch, config: dict, state: dict, today: date) -> tuple[list[dict], set[str], dict, dict]:
    """Reads every register for the configured years through `fetch(page_id, year) -> html`.
    Returns (docs to index, sources to remove, new state, counters). A register whose year
    cannot be read, or comes back empty when the state had acts for it, keeps its acts."""
    portal = config["portal"]
    years = max(1, int(config.get("years", 2)))
    old = state.get("items", {})
    items = dict(old)
    docs: list[dict] = []
    removed: set[str] = set()
    counts = {"acts": 0, "new": 0, "changed": 0, "removed": 0, "failed": 0}
    for register in config.get("registers", []):
        page_id = int(register["page_id"])
        for year in range(today.year - years + 1, today.year + 1):
            known = {s for s, it in old.items() if it["page_id"] == page_id and it["year"] == year}
            try:
                acts = parse_acts(fetch(page_id, year))
            except (httpx.HTTPError, ValueError) as e:
                log.warning(f"  register {page_id}, {year}: not read ({e}); its acts are kept")
                counts["failed"] += 1
                continue
            if not acts and known:
                log.warning(f"  register {page_id}, {year}: no act in the results (the state has {len(known)}); "
                            "page changed? its acts are kept")
                counts["failed"] += 1
                continue
            log.info(f"  register {page_id}, {year}: {len(acts)} acts")
            seen = set()
            for act in acts:
                item = act_item(register, year, act)
                source = act_source(portal, page_id, year, act["number"])
                seen.add(source)
                item["digest"] = _digest({k: v for k, v in item.items() if k != "digest"})
                if source not in old:
                    counts["new"] += 1
                elif old[source].get("digest") != item["digest"]:
                    counts["changed"] += 1
                else:
                    continue
                items[source] = item
                docs.append(act_doc(portal, source, item))
            gone = known - seen
            for source in gone:
                items.pop(source, None)
            removed |= gone
            counts["acts"] += len(acts)
    counts["removed"] = len(removed)
    return docs, removed, {"items": items}, counts


def latest_fact(config: dict, items: dict, today: date) -> dict | None:
    """Dynamic known fact: the last acts of each register (current year, then the previous one)."""
    portal = config["portal"]
    per_register = max(1, int(config.get("latest", 10)))
    lines = [(f"Ultimi atti pubblicati sul portale degli atti del Comune (elenco controllato il "
              f"{today:%d/%m/%Y}; per ogni atto numero, data e oggetto):")]
    for register in config.get("registers", []):
        page_id = int(register["page_id"])
        acts = sorted((it for it in items.values() if it["page_id"] == page_id),
                      key=lambda it: (it["year"], it["number"]), reverse=True)[:per_register]
        if not acts:
            continue
        lines.append("")
        lines.append(f"Ultime {register.get('plural') or 'atti'} ({portal}?page_id={page_id}):")
        for it in acts:
            d = date.fromisoformat(it["date"])
            subject = it["subject"] if len(it["subject"]) <= 220 else it["subject"][:217].rstrip() + "…"
            lines.append(f"- n. {it['number']} del {d:%d/%m/%Y}: {subject}")
    if len(lines) == 1:
        return None
    lines.append("")
    lines.append("Il testo completo di ogni atto si apre dalla pagina di ricerca del suo registro, indicando "
                 "numero e anno.")
    return {
        "id": FACT_ID,
        "keywords": config.get("keywords") or FACT_KEYWORDS,
        "require_any": config.get("require_any") or FACT_REQUIRE_ANY,
        "title": f"Ultime delibere e ultimi atti pubblicati (aggiornato automaticamente) - {SITE_NAME}",
        "source": portal,
        "category": "amministrazione",
        "updated": today.isoformat(),
        "text": "\n".join(lines),
    }


def run_acts(dry_run: bool = False) -> dict:
    """One run: registers → index and «latest acts» fact. Returns the counters."""
    config = ACTS_SOURCES
    if not config.get("portal") or not config.get("registers"):
        log.warning("No register of acts configured (crawl_extra.json → \"acts\")")
        return {"acts": 0, "new": 0, "changed": 0, "removed": 0, "failed": 0, "chunks": 0}
    portal = config["portal"].rstrip("/") + "/"
    config = {**config, "portal": portal}
    today = now_local().date()
    state = load_state()
    with httpx.Client(headers=_HEADERS, follow_redirects=False, timeout=60.0, trust_env=False) as client:
        docs, removed, new_state, counts = collect(
            lambda page_id, year: fetch_register(client, portal, page_id, year), config, state, today)
    log.info(f"Acts: {counts['acts']} read, {counts['new']} new, {counts['changed']} changed, "
             f"{counts['removed']} removed, {counts['failed']} registers not read")
    fact = latest_fact(config, new_state["items"], today)
    if dry_run:
        for d in docs[:5]:
            print(d["chunks"][0]["text"] if d["chunks"] else d["url"], "\n")
        if fact:
            print(fact["text"])
        return {**counts, "chunks": 0}
    inserted = 0
    if docs or removed:
        from indexer.indexer import _index_documents
        from indexer.vector_store import remove_sources
        if docs:
            _, inserted = _index_documents(docs, "ACTS", lambda d: d["chunks"], skip_existing=False)
        if removed:
            remove_sources(removed, keep_inbox=False)
    save_state(new_state)
    if fact:
        from scripts.council_sessions import upsert_fact
        upsert_fact(KNOWN_FACTS_DYNAMIC_FILE, fact)
    return {**counts, "chunks": inserted}


def main() -> int:
    ap = argparse.ArgumentParser(description="Registers of acts → one passage per act")
    ap.add_argument("--dry-run", action="store_true", help="read the registers and print, write nothing")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    counts = run_acts(dry_run=args.dry_run)
    return 1 if counts["failed"] and not counts["acts"] else 0


if __name__ == "__main__":
    sys.exit(main())
