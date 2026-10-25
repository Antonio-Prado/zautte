"""
Council sessions calendar, kept up to date by cron.

Reads the page where the municipality publishes the convocations of the municipal council
(one link per session, "O.d.G. Consiglio comunale del 24/10/2026", grouped under headings
such as "Convocazioni anno 2026"), works out the last session held and the next one
convened, and writes a known fact in KNOWN_FACTS_DYNAMIC_FILE (same format as
config/known_facts.json). The API rereads that file when it changes: no restart.

Configuration: the "council" key of config/crawl_extra.json
    {"convocations_url": "https://www.example.org/it/public_documents/convocazioni-consiglio-comunale",
     "link_pattern": "consiglio",            # regex a session link's text must match (default)
     "keywords": [...], "require_any": [...]} # optional: when the fact is injected
Run (hourly from cron, as the sync account):
    venv/bin/python -m scripts.council_sessions [--dry-run]
Exit codes: 0 written or unchanged, 1 fetch or configuration error, 2 no session found (the
previous file is kept).
"""

import argparse
import bisect
import contextlib
import grp
import html
import json
import logging
import os
import re
import sys
import tempfile
from datetime import date
from pathlib import Path
from urllib.parse import urljoin

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.privdrop import drop_to_sync_user

drop_to_sync_user()   # as root: become the sync account before config.settings reads .env

import httpx

from config.settings import (
    COUNCIL_SESSIONS,
    CRAWL_ALLOWED_DOMAINS,
    KNOWN_FACTS_DYNAMIC_FILE,
    SITE_NAME,
    now_local,
)
from crawler.netpolicy import get_limited

log = logging.getLogger("council_sessions")

FACT_ID = "council-sessions"
MONTHS = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
          "settembre", "ottobre", "novembre", "dicembre"]
WEEKDAYS = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
_MONTH_NO = {m: i + 1 for i, m in enumerate(MONTHS)}

_ANCHOR_RE = re.compile(r'<a\b[^>]*?href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_YEAR_MARK_RE = re.compile(r"\banno\s+(20\d{2})\b", re.IGNORECASE)
_DATE_NUM_RE = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](20\d{2})\b")
_DATE_TXT_RE = re.compile(r"\b(\d{1,2})°?\s+(" + "|".join(MONTHS) + r")(?:\s+(20\d{2}))?\b", re.IGNORECASE)
_YEAR_IN_URL_RE = re.compile(r"(?<!\d)(20\d{2})(?!\d)")

DEFAULT_KEYWORDS = [
    "consiglio", "consigli", "consiliare", "consiliari", "comunale", "seduta", "sedute", "convocazione",
    "convocazioni", "convocato", "convocata", "riunione", "riunito", "riunisce", "riunirsi", "ultimo", "ultima",
    "prossimo", "prossima", "quando", "calendario", "date", "data", "ordine", "giorno", "odg", "question", "time",
]
DEFAULT_REQUIRE_ANY = [
    "consiglio", "consigli", "consiliare", "consiliari", "seduta", "sedute", "convocazione", "convocazioni",
    "convocato", "convocata",
]


def _clean(text: str) -> str:
    text = html.unescape(_TAG_RE.sub(" ", text)).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def parse_sessions(page: str, base_url: str, link_pattern: str = "consiglio") -> list[dict]:
    """The sessions linked in the page: [{date, url, text, note}], one per date, oldest first.
    The year comes from the link text, else from the nearest "anno YYYY" heading above the link,
    else from the link address; a link without a year is skipped."""
    link_re = re.compile(link_pattern, re.IGNORECASE)
    marks = [(m.start(), int(m.group(1))) for m in _YEAR_MARK_RE.finditer(page)]
    mark_pos = [p for p, _ in marks]
    found: dict[date, dict] = {}
    for m in _ANCHOR_RE.finditer(page):
        href, text = m.group(1), _clean(m.group(2))
        if not link_re.search(text):
            continue
        d = None
        num = _DATE_NUM_RE.search(text)
        if num:
            day, month, year = int(num.group(1)), int(num.group(2)), int(num.group(3))
        else:
            txt = _DATE_TXT_RE.search(text)
            if not txt:
                continue
            day, month = int(txt.group(1)), _MONTH_NO[txt.group(2).lower()]
            if txt.group(3):
                year = int(txt.group(3))
            else:
                i = bisect.bisect_right(mark_pos, m.start()) - 1
                year = marks[i][1] if i >= 0 else None
                if year is None:
                    in_url = _YEAR_IN_URL_RE.search(href)
                    year = int(in_url.group(1)) if in_url else None
            if year is None:
                continue
        try:
            d = date(year, month, day)
        except ValueError:
            continue
        low = (text + " " + href).lower()
        note = ""
        if "question" in low:
            note = "question time"
        elif "seduta aperta" in low:
            note = "seduta aperta"
        elif "straordinari" in low:
            note = "seduta straordinaria"
        if d not in found:
            found[d] = {"date": d, "url": urljoin(base_url, html.unescape(href)), "text": text, "note": note}
    return [found[k] for k in sorted(found)]


def _long(d: date) -> str:
    return f"{WEEKDAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]} {d.year}"


def _short(s: dict) -> str:
    d = s["date"]
    out = f"{d.day} {MONTHS[d.month - 1]}"
    return f"{out} ({s['note']})" if s["note"] else out


def build_fact(sessions: list[dict], today: date, url: str, site_name: str,
               keywords: list[str] | None = None, require_any: list[str] | None = None) -> dict:
    """The known fact: last session held, next one convened, calendar of this year and the last."""
    past = [s for s in sessions if s["date"] <= today]
    future = [s for s in sessions if s["date"] > today]
    today_s = f"{today.day:02d}/{today.month:02d}/{today.year}"
    lines = [(f"Calendario delle sedute del Consiglio comunale ({site_name}), letto automaticamente dalla "
              f"pagina delle convocazioni del sito il {today_s}."), ""]
    if past:
        last = past[-1]
        when = "oggi, " if last["date"] == today else ""
        lines.append(f"Ultima seduta svolta: {when}{_long(last['date'])}"
                     + (f" ({last['note']})" if last["note"] else "") + f". Ordine del giorno: {last['url']}")
    else:
        lines.append("Nessuna seduta già svolta risulta nella pagina delle convocazioni.")
    if future:
        nxt = future[0]
        lines.append(f"Prossima seduta convocata: {_long(nxt['date'])}"
                     + (f" ({nxt['note']})" if nxt["note"] else "") + f". Ordine del giorno: {nxt['url']}")
        if len(future) > 1:
            lines.append("Sedute successive già convocate: " + ", ".join(_long(s["date"]) for s in future[1:]) + ".")
    else:
        lines.append(f"Prossima seduta: al {today_s} non è ancora pubblicata la convocazione di una seduta "
                     f"successiva. La convocazione, con l'ordine del giorno, compare nella pagina {url} di "
                     f"solito circa una settimana prima della seduta.")
    lines.append("")
    years = sorted({s["date"].year for s in sessions}, reverse=True)
    for y in years[:2]:
        of_year = [s for s in sessions if s["date"].year == y]
        label = "Sedute del " + str(y) + (" (anno in corso)" if y == today.year else "")
        lines.append(f"{label}, in ordine cronologico: " + ", ".join(_short(s) for s in of_year) + ".")
    if len(years) > 1 and years[1] < today.year:
        lines.append(f"ATTENZIONE: le date elencate sotto «Sedute del {years[1]}» sono dell'anno {years[1]}, "
                     f"non del {today.year}.")
    lines.append("")
    lines.append(f"Pagina delle convocazioni con gli ordini del giorno di tutte le sedute: {url}")
    return {
        "id": FACT_ID,
        "keywords": keywords or DEFAULT_KEYWORDS,
        "require_any": require_any or DEFAULT_REQUIRE_ANY,
        "title": ("Calendario delle sedute del Consiglio comunale: ultima seduta svolta e prossima seduta "
                  f"convocata (aggiornato automaticamente) - {site_name}"),
        "source": url,
        "category": "amministrazione",
        "updated": today.isoformat(),
        "text": "\n".join(lines),
    }


def _share_with_api(path: Path, mode: int) -> None:
    """The job runs as the sync account and the API as another one: the file (or folder) gets
    the shared group of data/ (SYNC_SHARED_GROUP, default zdata) and group read access, no
    access for others. Without that group (a single-user installation) the mode alone is set."""
    os.chmod(path, mode)
    name = os.environ.get("SYNC_SHARED_GROUP", "zdata")
    try:
        os.chown(path, -1, grp.getgrnam(name).gr_gid)
    except (KeyError, PermissionError, OSError):
        pass   # no such group, or not a member of it: the owner's group stays


def upsert_fact(path: Path, fact: dict, dry_run: bool = False) -> bool:
    """Replaces the fact with the same id in the file (other dynamic facts are kept). Returns
    True when the file was written, False when its content was already the same."""
    facts: list[dict] = []
    if path.exists():
        try:
            facts = [f for f in json.loads(path.read_text(encoding="utf-8")) if f.get("id") != fact["id"]]
        except (OSError, ValueError, AttributeError):
            facts = []   # unreadable or malformed: rebuilt from scratch
    facts.append(fact)
    new_text = json.dumps(facts, ensure_ascii=False, indent=1) + "\n"
    try:
        if path.read_text(encoding="utf-8") == new_text:
            return False
    except OSError:
        pass   # no file yet: written below
    if dry_run:
        return True
    if not path.parent.exists():
        path.parent.mkdir(parents=True)
        _share_with_api(path.parent, 0o750)
    fd, tmp = tempfile.mkstemp(prefix=".facts-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(new_text)
        _share_with_api(Path(tmp), 0o640)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    return True


def fetch(url: str) -> str:
    with httpx.Client(timeout=30, headers={"User-Agent": "Zautte council-sessions"}) as client:
        r = get_limited(client, url, CRAWL_ALLOWED_DOMAINS)
        r.raise_for_status()
        return r.text


def main() -> int:
    ap = argparse.ArgumentParser(description="Council sessions calendar → dynamic known fact")
    ap.add_argument("--dry-run", action="store_true", help="print the fact, write nothing")
    ap.add_argument("--html", help="parse this local file instead of downloading the page")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    url = (COUNCIL_SESSIONS.get("convocations_url") or "").strip()
    if not url:
        log.error("no council.convocations_url in config/crawl_extra.json: nothing to do")
        return 1
    try:
        page = Path(args.html).read_text(encoding="utf-8", errors="replace") if args.html else fetch(url)
    except Exception as exc:  # noqa: BLE001 - any network/policy error: keep the previous file
        log.error("could not read %s: %s", url, exc)
        return 1
    sessions = parse_sessions(page, url, COUNCIL_SESSIONS.get("link_pattern") or "consiglio")
    if not sessions:
        log.error("no session found in %s: the previous fact is kept", url)
        return 2
    today = now_local().date()
    fact = build_fact(sessions, today, url, SITE_NAME,
                      COUNCIL_SESSIONS.get("keywords"), COUNCIL_SESSIONS.get("require_any"))
    if args.dry_run:
        print(fact["text"])
    written = upsert_fact(KNOWN_FACTS_DYNAMIC_FILE, fact, dry_run=args.dry_run)
    last = [s for s in sessions if s["date"] <= today]
    nxt = [s for s in sessions if s["date"] > today]
    log.info("%d sessions; last %s; next %s; %s", len(sessions),
             last[-1]["date"].isoformat() if last else "-", nxt[0]["date"].isoformat() if nxt else "-",
             "written" if written and not args.dry_run else "unchanged" if not written else "dry run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
