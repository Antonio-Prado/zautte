"""
Who heads each office, as the pages of the offices say today, kept up to date by cron.

"Is Mr X still the head of the tax office?" was answered from the management plans of the past
years, which name whoever held the post then: hundreds of passages carry the old name, while the
page of the office, which names the head of today, did not come up in the search at all (58th for
"who is the head of the tax office?"; only the first 21 are considered). This job reads the pages
that list the offices, then the page of each one, takes the names under its «Responsabile» heading, the kind of unit, the unit it belongs to and the date the page was
last updated, and writes one known fact in KNOWN_FACTS_DYNAMIC_FILE (same format as
config/known_facts.json): every unit with the head its page names, or the statement that the
page names none. The API rereads that file when it changes: no restart.

The pages are those of the Italian municipal site template (Design Comuni): a list of cards
linking the units, and in each unit page an <h2> per section with the people as cards (<h3>).

Configuration: the "offices" key of config/crawl_extra.json
    {"index_urls": ["https://www.example.org/it/page/uffici"],   # the pages that list the units; add the list
                                                      # of the areas only if their pages name the head in that
                                                      # section: one that names it elsewhere would be listed
                                                      # as naming none
     "link_pattern": "/unita_organizzative/[^/?#]+$",  # regex the address of a unit must match (default)
     "head_label": "Responsabile",                     # heading of the section that names the head (default)
     "delay": 5,                                       # seconds between two requests (default)
     "keywords": [...], "require_any": [...],          # optional: when the fact is injected
     "ignore_phrases": [...]}                          # optional: phrases that do not ask for the head of an office
The fact is injected when the question has a word of the role («responsabile», «capo», …) and a
word of the units: a generic one («ufficio», «servizio»), one of the name of a unit or of a head,
or a keyword that config/offices.json gives for the page of a listed unit («tari» for the tax office).
«Responsabile del procedimento», «responsabile della protezione dei dati» and the like are other roles:
those phrases are taken out of the question before the words are looked for.
Run (daily from cron, as the sync account, through scripts/office_heads.sh):
    venv/bin/python -m scripts.office_heads [--dry-run]
Exit codes: 0 written or unchanged, 1 fetch or configuration error (a page that cannot be read or
is not the page of a unit), 2 no unit listed or no head named anywhere (in every case the previous
file is kept: the list is never written in part).
"""

import argparse
import json
import logging
import re
import sys
import time
import unicodedata
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urldefrag, urljoin, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.privdrop import drop_to_sync_user

drop_to_sync_user()   # as root: become the sync account before config.settings reads .env

import httpx
from bs4 import BeautifulSoup, Tag

from config.settings import (
    CRAWL_ALLOWED_DOMAINS,
    KNOWN_FACTS_DYNAMIC_FILE,
    OFFICE_HEADS,
    SITE_NAME,
    SITE_URL,
    now_local,
)
from crawler.netpolicy import get_limited
from scripts.council_sessions import MONTHS, upsert_fact

log = logging.getLogger("office_heads")

FACT_ID = "office-heads"
LINK_PATTERN = r"/unita_organizzative/[^/?#]+$"
HEAD_LABEL = "Responsabile"
KIND_LABEL = "Tipo di organizzazione"
PARENT_LABEL = "Unità organizzativa genitore"
DELAY_SECONDS = 5.0        # the site answers 429 after some tens of requests in a row
RETRY_WAITS = (30, 60, 120)   # seconds waited after a 429 or a 503, then the page is asked again
MAX_INDEX_PAGES = 20       # pages of one list followed («?page=2», …)
MAX_UNITS = 300
MAX_NAME = 120
OFFICES_FILE = Path(__file__).resolve().parent.parent / "config" / "offices.json"

_UPDATED_RE = re.compile(r"ultimo aggiornamento\s*:?\s*(\d{1,2})°?\s+(" + "|".join(MONTHS) + r")\s+(20\d{2})",
                         re.IGNORECASE)
_WORD_RE = re.compile(r"\w+")

# The fact is injected when the question has a word of the role AND one of the units: a generic
# one («ufficio», «servizio»), a word of the name of a unit or of the name of a head.
DEFAULT_KEYWORDS = sorted({
    "responsabile", "responsabili", "capo", "capoufficio", "capufficio", "caposervizio", "caposettore",
    "dirige", "dirigono", "direttore", "direttrice", "referente", "coordinatore", "coordinatrice",
    "funzionario", "funzionaria", "head", "heads", "manager", "chief", "director", "responsible"})
UNIT_WORDS = {"ufficio", "uffici", "servizio", "servizi", "area", "aree", "settore", "settori", "unità", "unita",
              "sportello", "office", "offices", "department", "departments", "service", "unit"}
# Roles that are not the head of an office: removed from the question before matching (see api/rag.py)
DEFAULT_IGNORE_PHRASES = [
    "responsabile del procedimento", "responsabili del procedimento", "responsabile unico del procedimento",
    "responsabile unico del progetto", "responsabile del trattamento", "responsabile della protezione dei dati",
    "responsabile protezione dati", "responsabile della prevenzione della corruzione",
    "responsabile della trasparenza", "responsabile civile", "data protection officer"]
_STOPWORDS = {"alla", "alle", "agli", "dalla", "dalle", "degli", "della", "delle", "dello", "dell", "nella",
              "nelle", "sulla", "sulle", "come", "sono"}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def _key(text: str) -> str:
    return _clean(text).casefold()


def _soup(page: str) -> BeautifulSoup:
    soup = BeautifulSoup(page or "", "lxml")
    for a in soup.find_all("a", href="#"):
        a.decompose()   # the targets of the in-page index: a label of the template, not content
    return soup


def parse_index(page: str, base_url: str, link_pattern: str = LINK_PATTERN) -> tuple[list[tuple[str, str]], list[str]]:
    """The units a list page links, [(name, address)] in page order, and the other pages of the
    same list («?page=2»). The name is the text of the first link to that address."""
    link_re = re.compile(link_pattern)
    base = urlsplit(base_url)
    units: dict[str, str] = {}
    more: list[str] = []
    for a in _soup(page).find_all("a", href=True):
        url = urldefrag(urljoin(base_url, a["href"])).url
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            continue
        if link_re.search(parts.path):
            name = _clean(a.get_text(" "))[:MAX_NAME]
            if name and not units.get(url):
                units[url] = name
            units.setdefault(url, "")
        elif ((parts.netloc, parts.path) == (base.netloc, base.path) and url not in more
              and parse_qs(parts.query).get("page", ["1"])[0] not in ("", "1", *parse_qs(base.query).get("page", []))):
            more.append(url)   # «?page=2» of this list; «?page=1» is the list itself
    return [(name, url) for url, name in units.items() if name], more


def _sections(soup: BeautifulSoup) -> dict[str, dict]:
    """Heading (casefolded) → {"names": the <h3> between that <h2> and the next one, "text": the
    text between them}. A unit page has one <h2> per section; the people are cards with an <h3>."""
    out: dict[str, dict] = {}
    for h2 in soup.find_all("h2"):
        names: list[str] = []
        texts: list[str] = []
        for el in h2.next_elements:
            if isinstance(el, Tag):
                if el.name == "h2":
                    break
                if el.name == "h3":
                    name = _clean(el.get_text(" "))[:MAX_NAME]
                    if name and name not in names:
                        names.append(name)
            elif not any(p is h2 for p in el.parents):
                texts.append(str(el))
        out.setdefault(_key(h2.get_text(" ")), {"names": names, "text": _clean(" ".join(texts))})
    return out


def parse_unit(page: str, head_label: str = HEAD_LABEL) -> dict:
    """What the page of a unit states: {"heads": [names], "kind", "parent", "updated"}; the head
    section may be missing or empty (heads: []). ValueError when the page has neither the head
    section nor the kind of unit: an error page, or a layout this job does not know."""
    soup = _soup(page)
    sections = _sections(soup)
    head, kind = sections.get(_key(head_label)), sections.get(_key(KIND_LABEL))
    if head is None and kind is None:
        raise ValueError(f"neither «{head_label}» nor «{KIND_LABEL}» among the headings")
    parent = sections.get(_key(PARENT_LABEL)) or {"names": [], "text": ""}
    kind_text = kind["text"] if kind and len(kind["text"]) <= 40 else ""
    updated = ""
    m = _UPDATED_RE.search(_clean(soup.get_text(" ")))
    if m:
        updated = f"{int(m[1])} {m[2].lower()} {m[3]}"
    return {"heads": head["names"] if head else [], "kind": kind_text,
            "parent": parent["names"][0] if parent["names"] else "", "updated": updated}


def read_units(get, index_urls: list[str], link_pattern: str = LINK_PATTERN, head_label: str = HEAD_LABEL) -> list[dict]:
    """Every unit of the lists with what its page states. `get(url)` returns the page; an
    httpx.HTTPStatusError 404 or 410 means the unit is gone and is skipped, any other error
    is the caller's (the previous fact is kept)."""
    listed: dict[str, str] = {}
    for index in index_urls:
        todo, seen = [index], set()
        while todo and len(seen) < MAX_INDEX_PAGES:
            url = todo.pop(0)
            seen.add(url)
            units, more = parse_index(get(url), url, link_pattern)
            for name, unit_url in units:
                listed.setdefault(unit_url, name)
            todo += [u for u in more if u not in seen and u not in todo]
    out: list[dict] = []
    for url, name in list(listed.items())[:MAX_UNITS]:
        try:
            page = get(url)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (404, 410):
                log.warning("%s: listed but gone (%d), skipped", url, exc.response.status_code)
                continue
            raise
        try:
            unit = parse_unit(page, head_label)
        except ValueError as exc:
            raise ValueError(f"{url}: {exc}") from exc
        out.append({"name": name, "url": url, **unit})
    return out


def _day(d: date) -> str:
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def _plain(word: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", word) if not unicodedata.combining(c))


def office_keywords(units: list[dict], path: Path = OFFICES_FILE, site_url: str = SITE_URL) -> set[str]:
    """The one-word keywords config/offices.json gives for the pages of the listed units: the names
    people use for an office («tari», «anagrafe») that its page does not carry in its title."""
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    listed = {u["url"].rstrip("/") for u in units}
    words: set[str] = set()
    for e in entries if isinstance(entries, list) else []:
        if not isinstance(e, dict):
            continue
        url = site_url.rstrip("/") + str(e["path"]) if e.get("path") else str(e.get("url", ""))
        if url.rstrip("/") in listed:
            words.update(k.lower() for k in e.get("keywords", []) if isinstance(k, str) and _WORD_RE.fullmatch(k))
    return words


def unit_words(units: list[dict], extra: set[str] | None = None) -> list[str]:
    """The words that tell a question is about a unit: the generic ones, those of the names of the
    units and of their heads (four letters or more), with and without accents, and `extra`."""
    words = set(UNIT_WORDS) | set(extra or ())
    for u in units:
        for text in [u["name"], *u["heads"]]:
            for w in _WORD_RE.findall(text.lower()):
                if len(w) >= 4 and not w.isdigit() and w not in _STOPWORDS:
                    words.update({w, _plain(w)})
    return sorted(words)


def _entry(u: dict) -> str:
    about = "; ".join(p for p in [u["kind"], f"fa parte di: {u['parent']}" if u["parent"] else ""] if p)
    line = f"- {u['name']}" + (f" ({about})" if about else "") + ": "
    if u["heads"]:
        line += ("responsabile " if len(u["heads"]) == 1 else "responsabili ") + ", ".join(u["heads"])
    else:
        line += "la pagina non indica un responsabile"
    page = "Pagina" + (f" (ultimo aggiornamento: {u['updated']})" if u["updated"] else "")
    return f"{line}. {page}: {u['url']}"


def build_fact(units: list[dict], today: date, index_urls: list[str], site_name: str,
               keywords: list[str] | None = None, require_any: list[str] | None = None,
               extra_words: set[str] | None = None, ignore_phrases: list[str] | None = None) -> dict:
    """The known fact: every unit with the head its page names today. `extra_words` join the
    words of the units in the gate, unless the configuration gives its own `require_any`."""
    lines = [(f"Responsabili degli uffici del {site_name}: elenco letto automaticamente il {_day(today)} "
              f"dalle pagine degli uffici del sito istituzionale ({' ; '.join(index_urls)}). Per ogni ufficio è "
              "riportato il nome che la sua pagina indica oggi alla voce «Responsabile», con la data dell'ultimo "
              "aggiornamento della pagina."), ""]
    lines += [_entry(u) for u in units]
    lines.append("")
    lines.append("Come rispondere: a chi chiede chi è il responsabile di un ufficio, o se una persona ne è ancora il "
                 "responsabile, rispondi con il nome scritto in questo elenco e con il link alla pagina dell'ufficio. "
                 "Se la domanda nomina una persona diversa da quella indicata qui, di' chi è il responsabile indicato "
                 "oggi dal sito, senza dire da quando né quale incarico abbia ora l'altra persona: il sito non lo dice. "
                 "I documenti meno recenti (piani esecutivi di gestione, delibere, notizie) possono riportare altri "
                 "nomi perché riguardano incarichi di quel periodo. Se la pagina di un ufficio non indica un "
                 "responsabile, dillo e rimanda alla pagina dell'ufficio, senza ricavare il nome da altri documenti. "
                 "Per gli uffici di questo elenco non indicare come responsabile un nome diverso da quello scritto qui.")
    return {
        "id": FACT_ID,
        "keywords": keywords or DEFAULT_KEYWORDS,
        "require_any": require_any or unit_words(units, extra_words),
        "ignore_phrases": DEFAULT_IGNORE_PHRASES if ignore_phrases is None else ignore_phrases,
        "title": ("Responsabili degli uffici: chi è il responsabile di ogni ufficio secondo il sito "
                  f"(aggiornato automaticamente) - {site_name}"),
        "source": index_urls[0],
        "category": "amministrazione",
        "updated": today.isoformat(),
        # every address in the text is a page this run has read: the API does not try them again
        # (api/linkcheck.py), which would be some forty requests at once to a site that refuses them
        "links_checked": True,
        "text": "\n".join(lines).rstrip() + "\n",
    }


def paced_get(client: httpx.Client, delay: float = DELAY_SECONDS, sleep=time.sleep):
    """get(url) → the text of the page, under the crawl policy, `delay` seconds after the previous
    request. A 429 or a 503 is asked again after a wait (Retry-After if the site gives one within
    five minutes), RETRY_WAITS times; any other error status raises httpx.HTTPStatusError."""
    state = {"first": True}

    def get(url: str) -> str:
        for wait in (*RETRY_WAITS, None):
            if not state["first"]:
                sleep(delay)
            state["first"] = False
            r = get_limited(client, url, CRAWL_ALLOWED_DOMAINS)
            if r.status_code in (429, 503) and wait is not None:
                after = r.headers.get("retry-after", "")
                wait = int(after) if after.isdigit() and 0 < int(after) <= 300 else wait
                log.warning("%s: %d, asked again in %d s", url, r.status_code, wait)
                sleep(wait)
                continue
            r.raise_for_status()
            return r.text
        raise AssertionError("unreachable")   # the last round returns or raises

    return get


def main() -> int:
    ap = argparse.ArgumentParser(description="Heads of the offices (pages of the site) → dynamic known fact")
    ap.add_argument("--dry-run", action="store_true", help="print the fact, write nothing")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    index_urls = OFFICE_HEADS.get("index_urls") or []
    index_urls = [u.strip() for u in ([index_urls] if isinstance(index_urls, str) else index_urls) if u.strip()]
    if not index_urls:
        log.error("no offices.index_urls in config/crawl_extra.json: nothing to do")
        return 1
    try:
        delay = float(OFFICE_HEADS.get("delay", DELAY_SECONDS))
        with httpx.Client(timeout=30, headers={"User-Agent": "Zautte office-heads"}, trust_env=False) as client:
            units = read_units(paced_get(client, delay), index_urls,
                               OFFICE_HEADS.get("link_pattern") or LINK_PATTERN,
                               OFFICE_HEADS.get("head_label") or HEAD_LABEL)
    except Exception as exc:  # noqa: BLE001 - any network/policy/format error: keep the previous file
        log.error("could not read the offices of %s: %s", index_urls[0], exc)
        return 1
    named = [u for u in units if u["heads"]]
    if not named:
        log.error("%s: %d units listed, none with a head: the previous fact is kept", index_urls[0], len(units))
        return 2
    fact = build_fact(units, now_local().date(), index_urls, SITE_NAME,
                      OFFICE_HEADS.get("keywords"), OFFICE_HEADS.get("require_any"), office_keywords(units),
                      OFFICE_HEADS.get("ignore_phrases"))
    if args.dry_run:
        print(fact["text"])
    written = upsert_fact(KNOWN_FACTS_DYNAMIC_FILE, fact, dry_run=args.dry_run)
    log.info("%d units, %d with a head, %d without (%d characters); %s", len(units), len(named),
             len(units) - len(named), len(fact["text"]),
             "written" if written and not args.dry_run else "unchanged" if not written else "dry run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
