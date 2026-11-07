"""
Figures of the transparency portal that citizens ask for, kept up to date by cron as dynamic known facts.

«Quanto guadagna il sindaco?», «chi è il responsabile anticorruzione?», «in quanti giorni paga il
Comune?», «quanti dipendenti ha il Comune?» have one answer each, in one document of the
«Amministrazione trasparente» that is published again every year. The search over the index finds
the document, but often an older version of it, and the figure is in the attachment, not in the
heading. This job reads the latest version of each of these documents from the portal (the same
pages scripts/amt_sync.py reads) and writes what it finds as known facts in
KNOWN_FACTS_DYNAMIC_FILE (same format as config/known_facts.json), one per subject:
  pay       the pay of the mayor, the councillors and the members of the council (an .ods/.xls table)
  rpct      the officer for the prevention of corruption and for transparency (name and contacts)
  payments  the yearly indicator of the timeliness of payments, with its quarters (PDF)
  staff     the staff in service at the end of the year (the sentence of the page)
A subject whose document cannot be read keeps its previous fact. The API rereads the file when it
changes: no restart.

Configuration: the "amt_facts" key of config/crawl_extra.json (portal and page_id come from "amt"):
    {"pay": {"voceamt": 173}, "rpct": {"voceamt": 513}, "payments": {"voceamt": 535}, "staff": {"voceamt": 24}}
each subject may add "keywords" and "require_any" (when the fact is injected) and "title" (the
title of the fact). A subject left out is not read.
Run (daily from cron, as the sync account, through scripts/transparency_facts.sh):
    venv/bin/python -m scripts.transparency_facts [--dry-run] [--only pay|rpct|payments|staff]
Exit codes: 0 at least one subject written or unchanged, 1 configuration error or every subject failed.
"""

import argparse
import logging
import re
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.privdrop import drop_to_sync_user

drop_to_sync_user()   # as root: become the sync account before config.settings reads .env

import httpx

from config.settings import (
    AMT_FACTS,
    AMT_SOURCES,
    CRAWL_DELAY_SECONDS,
    KNOWN_FACTS_DYNAMIC_FILE,
    SITE_NAME,
    now_local,
)
from indexer.office import extract
from scripts.amt_sync import _HEADERS, _get, _pdf_text, page_url, parse_page, table_rows
from scripts.council_sessions import MONTHS, upsert_fact

log = logging.getLogger("transparency_facts")

SUBJECTS = ("pay", "rpct", "payments", "staff")
_YEAR_RE = re.compile(r"\b(20[0-4]\d)\b")
_MAX_ATTACHMENT = 400_000      # characters read from one attachment
_AMOUNT_RE = re.compile(r"-?\d{1,3}(?:\.\d{3})*(?:,\d+)?")
_INDICATOR_RE = re.compile(r"TEMPESTIVIT.{0,60}?\(B\s*/\s*A\)\s*(-?\d+(?:[.,]\d+)?)", re.IGNORECASE | re.DOTALL)
_STAFF_RE = re.compile(r"personale effettivo in servizio era\s+(\d+)", re.IGNORECASE)
_STAFF_DATE_RE = re.compile(r"\bal\s+(\d{1,2}\s+\w+\s+20\d\d)\b", re.IGNORECASE)
_QUARTERS = ("primo", "secondo", "terzo", "quarto")

# When each fact is injected: a word of `keywords` and a word of `require_any` in the question
DEFAULT_WORDS = {
    "pay": (["indennità", "indennita", "compenso", "compensi", "stipendio", "stipendi", "guadagna", "guadagnano",
             "guadagno", "retribuzione", "retribuzioni", "emolumenti", "gettone", "gettoni", "percepisce",
             "percepiscono", "paga", "pagato", "pagati", "costa", "costano", "costo", "soldi", "euro",
             "sindaco", "vicesindaco", "assessore", "assessori", "assessora", "consigliere", "consiglieri",
             "consigliera", "amministratori", "amministratore", "giunta", "consiglio", "politici"],
            ["indennità", "indennita", "compenso", "compensi", "stipendio", "stipendi", "guadagna", "guadagnano",
             "guadagno", "retribuzione", "retribuzioni", "emolumenti", "gettone", "gettoni", "percepisce",
             "percepiscono"]),
    "rpct": (["anticorruzione", "corruzione", "trasparenza", "rpct", "responsabile", "prevenzione", "whistleblowing",
              "segnalazione", "illecito", "illeciti"],
             ["anticorruzione", "corruzione", "rpct", "prevenzione", "whistleblowing"]),
    "payments": (["tempestività", "tempestivita", "pagamenti", "pagamento", "paga", "pagano", "fatture", "fattura",
                  "fornitori", "fornitore", "ritardo", "ritardi", "indicatore", "giorni", "scadenza"],
                 ["tempestività", "tempestivita", "fatture", "fattura", "fornitori", "fornitore", "indicatore"]),
    "staff": (["dipendenti", "dipendente", "personale", "organico", "dotazione", "dirigenti", "dirigente",
               "impiegati", "lavorano", "lavoratori", "assunti", "quanti", "quante"],
              # the money words only count with a staff word among the keywords («quanto costa la mensa» does not fire)
              ["dipendenti", "dipendente", "organico", "dotazione", "dirigenti", "impiegati", "lavoratori",
               "speso", "spesa", "spese", "costo", "costi", "costa", "costano", "retribuzioni", "stipendi",
               "stipendio", "retribuzione"]),
}


def _day(d: date) -> str:
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def doc_year(doc: dict) -> int:
    """The year the document is about: the last one in its title («anno 2025», «2022/2024»), 0 if none."""
    years = [int(y) for y in _YEAR_RE.findall(doc.get("title", ""))]
    return max(years) if years else 0


def latest_doc(docs: list[dict], must: str = "") -> dict | None:
    """The document with the latest year in its title, then the latest published (documents without
    a year in the title, such as the officer's name, count by publication); `must` (a regex)
    narrows the documents by title."""
    candidates = [d for d in docs if not must or re.search(must, d.get("title", ""), re.IGNORECASE)]
    if not candidates:
        return None
    return max(candidates, key=lambda d: (doc_year(d), d.get("published", "")))


# --------------------------------------------------------------------------- pay

def pay_lines(text: str) -> list[str]:
    """The rows of the table of the administrators' pay (one line per person) from the text of the
    .ods/.xls: «Nome Cognome, ruolo: Indennità di carica € 51.272,31; …; Totale € 70.540,27»."""
    headers: list[str] = []
    out = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.split("\t")]
        if len(cells) < 4:
            continue
        if any("indennit" in c.lower() for c in cells) and not any(_AMOUNT_RE.fullmatch(c.replace("€", "").strip()) for c in cells if c):
            headers = cells
            continue
        if not headers or not cells[1] or not re.match(r"\d+\.?$", cells[0]):
            continue
        name, role = cells[1], cells[2]
        parts = []
        for i in range(3, min(len(cells), len(headers))):
            value = cells[i].replace("€", "").strip()
            if not value or value.startswith("/") or not _AMOUNT_RE.fullmatch(value):
                continue
            label = re.sub(r"\s+", " ", headers[i]).strip()
            label = re.sub(r"\s*\(.*?\)\s*", " ", label).strip()
            label = re.sub(r"\s+al \d\d/\d\d/\d{4}$", "", label)
            parts.append(f"{label} € {value}")
        if parts:
            out.append(f"- {name}, {role}: " + "; ".join(parts))
    return out


def build_pay(doc: dict, lines: list[str], year: int, section_url: str, today: date, site: str, cfg: dict) -> dict:
    text = [(f"Compensi degli amministratori del {site} (sindaco, vicesindaco, assessori, consiglieri comunali) "
             f"nell'anno {year}, letti automaticamente il {_day(today)} dal documento «{doc['title']}» della sezione "
             f"«Titolari di incarichi politici» dell'Amministrazione Trasparente ({section_url}). Importi lordi in "
             f"euro, riferiti all'anno {year}: l'indennità di carica è quella mensile/annuale percepita per il "
             f"ruolo, il totale è quanto elargito dal Comune nell'anno (indennità, fine mandato, gettoni, "
             f"missioni)."), ""]
    text += lines
    text += ["", ("Come rispondere: a chi chiede quanto guadagna il sindaco, un assessore o un consigliere, dai gli "
                  f"importi dell'anno {year} di questo elenco con il nome della persona e il ruolo, e indica il "
                  "documento. Per un anno diverso rimanda alla stessa sezione. Non stimare importi che non sono in "
                  "questo elenco.")]
    return _fact("pay", cfg, f"Compensi del sindaco, degli assessori e dei consiglieri comunali {year} "
                 f"(aggiornato automaticamente) - {site}", section_url, today, "\n".join(text))


# --------------------------------------------------------------------------- rpct

def build_rpct(doc: dict, section_url: str, today: date, site: str, cfg: dict) -> dict:
    who = doc["title"].strip()
    desc = re.sub(r"\s+", " ", doc.get("description", "")).strip()
    text = [(f"Responsabile della prevenzione della corruzione e della trasparenza (RPCT) del {site}: {who}, "
             f"come pubblicato nella sezione «Prevenzione della corruzione» dell'Amministrazione Trasparente "
             f"({section_url}), letta automaticamente il {_day(today)}.")]
    if desc:
        text.append(f"Dati pubblicati: {desc}")
    if doc.get("attachments"):
        text.append("Documenti allegati: " + "; ".join(a["name"] for a in doc["attachments"][:6]) + ".")
    text.append("Come rispondere: indica il nome e i contatti pubblicati; per le segnalazioni di illeciti "
                "(whistleblowing) e i piani anticorruzione rimanda alla stessa sezione.")
    return _fact("rpct", cfg, f"Responsabile anticorruzione e trasparenza (RPCT) (aggiornato automaticamente) - {site}",
                 section_url, today, "\n".join(text))


# --------------------------------------------------------------------------- payments

def payment_values(texts: list[tuple[str, str]]) -> dict[str, float]:
    """{attachment name: indicator} for the attachments whose text carries the indicator (B/A)."""
    out = {}
    for name, text in texts:
        m = _INDICATOR_RE.search(text)
        if m:
            out[name] = float(m[1].replace(".", "").replace(",", "."))
    return out


def _quarter_of(name: str) -> int:
    low = name.lower()
    for i, q in enumerate(_QUARTERS, 1):
        if q in low:
            return i
    return 0


def build_payments(doc: dict, values: dict[str, float], year: int, previous: tuple[int, float] | None,
                   section_url: str, today: date, site: str, cfg: dict) -> dict:
    annual = [(n, v) for n, v in values.items() if not _quarter_of(n)]
    quarters = sorted(((_quarter_of(n), v) for n, v in values.items() if _quarter_of(n)))
    text = [(f"Indicatore di tempestività dei pagamenti del {site}, anno {year} (sezione «Pagamenti "
             f"dell'amministrazione» dell'Amministrazione Trasparente, {section_url}, letta automaticamente il "
             f"{_day(today)}). L'indicatore è il ritardo medio di pagamento delle fatture, in giorni, pesato sugli "
             f"importi, rispetto alla scadenza di legge (30 giorni): un valore negativo vuol dire che in media il "
             f"Comune paga prima della scadenza, un valore positivo che paga in ritardo."), ""]
    if annual:
        text.append(f"- Indicatore annuale {year}: {_days(annual[0][1])}.")
    else:
        text.append(f"- Indicatore annuale {year}: non ancora pubblicato (l'anno non è concluso); i trimestri "
                    f"pubblicati finora sono qui sotto.")
    for q, v in quarters:
        text.append(f"- {_QUARTERS[q - 1].capitalize()} trimestre {year}: {_days(v)}.")
    if previous:
        text.append(f"- Anno precedente ({previous[0]}): {_days(previous[1])}.")
    text += ["", ("Come rispondere: dai il valore annuale con il suo significato (in anticipo o in ritardo rispetto "
                  "ai 30 giorni) e, se chiesto, i trimestri; i file PDF con il calcolo sono nella pagina indicata. "
                  "Non confondere questo indicatore con i termini di pagamento scritti nei singoli contratti.")]
    return _fact("payments", cfg, f"Indicatore di tempestività dei pagamenti {year} (aggiornato automaticamente) - {site}",
                 section_url, today, "\n".join(text))


def _days(v: float) -> str:
    n = f"{abs(v):.2f}".replace(".", ",")
    if v < 0:
        return f"{n} giorni in anticipo rispetto alla scadenza (valore {str(v).replace('.', ',')})"
    if v > 0:
        return f"{n} giorni di ritardo medio (valore {str(v).replace('.', ',')})"
    return "0 giorni (pagamenti in media alla scadenza)"


# --------------------------------------------------------------------------- staff

def staff_total(description: str) -> tuple[int, str] | None:
    m = _STAFF_RE.search(description)
    if not m:
        return None
    d = _STAFF_DATE_RE.search(description)
    return int(m[1]), (d[1] if d else "")


_SPENDING_RE = re.compile(r"Spese per retribuzioni lorde", re.IGNORECASE)
_SPENDING_WINDOW = (300, 2200)   # characters kept before and after the first table of the yearly account


def spending_extract(text: str) -> str:
    """The part of the yearly account of the staff («Conto annuale») with the three-year table of the
    gross pay by category: raw text, enough for the model to read the totals."""
    m = _SPENDING_RE.search(text)
    if not m:
        return ""
    start = max(0, m.start() - _SPENDING_WINDOW[0])
    return re.sub(r"[ \t]+", " ", text[start:m.start() + _SPENDING_WINDOW[1]]).strip()


def build_staff(doc: dict, total: int, when: str, section_url: str, today: date, site: str, cfg: dict,
                spending: str = "") -> dict:
    when = when or f"31 dicembre {doc_year(doc)}"
    text = [(f"Personale del {site}: al {when} il totale del personale effettivo in servizio era di {total} "
             f"persone, tra dipendenti e dirigenti (documento «{doc['title']}» della sezione «Dotazione organica» "
             f"dell'Amministrazione Trasparente, {section_url}, letto automaticamente il {_day(today)})."),
            re.sub(r"\s+", " ", doc.get("description", "")).strip()]
    if doc.get("attachments"):
        text.append("Documenti allegati, con il conto annuale del personale e le spese: "
                    + "; ".join(a["name"] for a in doc["attachments"][:10]) + ".")
    if spending:
        text += ["", ("Spesa per il personale, dal «Conto annuale del personale e delle relative spese sostenute» "
                      "allegato alla stessa pagina (importi in euro, tabella riepilogativa degli ultimi tre anni per "
                      "categoria: le colonne sono gli anni indicati nell'intestazione, le spese per retribuzioni lorde "
                      "sono quelle delle tabelle 12 e 13 del conto annuale):"), spending]
    text += ["", ("Come rispondere: a chi chiede quanti dipendenti ha il Comune dai questo numero con la data; a chi "
                  "chiede quanto spende per il personale dai le cifre della tabella con l'anno e la categoria, "
                  "senza sommare categorie se la tabella non riporta un totale; per il resto rimanda ai documenti "
                  "della pagina indicata.")]
    return _fact("staff", cfg, f"Quanti dipendenti ha il Comune: dotazione organica (aggiornato automaticamente) - {site}",
                 section_url, today, "\n".join(text))


# --------------------------------------------------------------------------- common

def _fact(subject: str, cfg: dict, title: str, section_url: str, today: date, text: str) -> dict:
    keywords, require_any = DEFAULT_WORDS[subject]
    return {
        "id": f"amt-{subject}",
        "keywords": cfg.get("keywords") or keywords,
        "require_any": cfg.get("require_any") or require_any,
        "title": cfg.get("title") or title,
        "source": section_url,
        "category": "documento",
        "updated": today.isoformat(),
        "text": text.rstrip() + "\n",
    }


def _attachment_text(get_bytes, att: dict, max_chars: int = _MAX_ATTACHMENT) -> str:
    return extract(att["name"], get_bytes(att["url"]), max_chars, _pdf_text) or ""


def section_docs(fetch, post, config: dict, voceamt: int, section_url: str, must: str = "") -> list[dict]:
    """The documents of a section: the blocks of the page and, when the section lists them through a
    search table, the detail page of the most recent row whose title matches `must` (the only one
    these facts need)."""
    page = parse_page(fetch(section_url), section_url)
    docs = list(page["docs"])
    if page["table"] is not None and post is not None:
        rows = table_rows(post, config, voceamt, page["table"], section_url)
        latest = latest_doc([{**r, "attachments": [], "published": ""} for r in rows if r.get("detail")], must)
        if latest:
            detail = parse_page(fetch(latest["detail"]), latest["detail"])
            docs += detail["docs"][:1]
    return docs


def read_subject(subject: str, cfg: dict, config: dict, fetch, get_bytes, today: date, site: str,
                 post=None) -> dict | None:
    """The fact of one subject, read from the portal; None when the document is missing or unreadable."""
    section_url = page_url(config["portal"], int(config["page_id"]), int(cfg["voceamt"]))
    must = {"pay": r"compens|indennit|organi di indirizzo", "staff": r"dotazione organica"}.get(subject, "")
    docs = section_docs(fetch, post, config, int(cfg["voceamt"]), section_url, must)
    if subject == "pay":
        doc = latest_doc(docs, must)
        if not doc:
            return None
        atts = sorted(doc["attachments"], key=lambda a: (not a["name"].lower().endswith(".ods"),
                                                         not a["name"].lower().endswith((".xlsx", ".xls"))))
        for att in atts[:2]:
            lines = pay_lines(_attachment_text(get_bytes, att))
            if lines:
                return build_pay(doc, lines, doc_year(doc), section_url, today, site, cfg)
        log.warning("pay: no table of amounts in the attachments of «%s»", doc["title"])
        return None
    if subject == "rpct":
        doc = latest_doc(docs)
        return build_rpct(doc, section_url, today, site, cfg) if doc else None
    if subject == "payments":
        doc = latest_doc(docs)
        if not doc:
            return None
        year = doc_year(doc)
        values = payment_values([(a["name"], _attachment_text(get_bytes, a, 20_000)) for a in doc["attachments"][:6]])
        if not values:
            log.warning("payments: no indicator in the attachments of «%s»", doc["title"])
            return None
        previous = None
        prev_doc = latest_doc([d for d in docs if doc_year(d) == year - 1])
        if prev_doc:
            annual = [a for a in prev_doc["attachments"] if not _quarter_of(a["name"])][:1]
            prev = payment_values([(a["name"], _attachment_text(get_bytes, a, 20_000)) for a in annual])
            if prev:
                previous = (year - 1, next(iter(prev.values())))
        return build_payments(doc, values, year, previous, section_url, today, site, cfg)
    if subject == "staff":
        doc = latest_doc(docs, must)
        if not doc:
            return None
        found = staff_total(doc.get("description", ""))
        if not found:
            log.warning("staff: no total in the description of «%s»", doc["title"])
            return None
        spending = ""
        account = [a for a in doc["attachments"] if re.search(r"conto annuale", a["name"], re.IGNORECASE)][:1]
        if account:
            spending = spending_extract(_attachment_text(get_bytes, account[0], 200_000))
        return build_staff(doc, found[0], found[1], section_url, today, site, cfg, spending)
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Figures of the transparency portal → dynamic known facts")
    ap.add_argument("--dry-run", action="store_true", help="print the facts, write nothing")
    ap.add_argument("--only", choices=SUBJECTS, help="one subject only")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    subjects = [s for s in SUBJECTS if isinstance(AMT_FACTS.get(s), dict) and AMT_FACTS[s].get("voceamt")
                and (not args.only or s == args.only)]
    if not subjects or not AMT_SOURCES.get("portal") or not AMT_SOURCES.get("page_id"):
        log.error("no amt_facts.<subject>.voceamt (or amt.portal/page_id) in config/crawl_extra.json: nothing to do")
        return 1
    config = {**AMT_SOURCES, "portal": AMT_SOURCES["portal"].rstrip("/") + "/"}
    today = now_local().date()
    done = 0
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

        for subject in subjects:
            try:
                fact = read_subject(subject, AMT_FACTS[subject], config, fetch, get_bytes, today, SITE_NAME, post)
            except Exception as exc:  # noqa: BLE001 - any network/format error: keep the previous fact
                log.error("%s: not read (%s)", subject, exc)
                continue
            if fact is None:
                log.error("%s: no document found in section %s", subject, AMT_FACTS[subject]["voceamt"])
                continue
            if args.dry_run:
                print(f"===== {subject}\n{fact['text']}")
            written = upsert_fact(KNOWN_FACTS_DYNAMIC_FILE, fact, dry_run=args.dry_run)
            log.info("%s: %d characters; %s", subject, len(fact["text"]),
                     "written" if written and not args.dry_run else "unchanged" if not written else "dry run")
            done += 1
    return 0 if done else 1


if __name__ == "__main__":
    sys.exit(main())
