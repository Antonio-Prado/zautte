"""
«Amministrazione trasparente» on a Nuvola portal (PalItalsoft, itaFrontOffice), section by section.

The transparency tree is made of server-rendered pages (`?page_id=<P>&voceamt=<N>`): a page lists
its sub-sections as tiles («Accedi alla sezione») and its documents as blocks with a title, a
description and the attachments. The link of an attachment carries a token that changes at every
visit, so it cannot be stored or shown; its MD5 fingerprint, printed next to it, does not change.
This job walks the configured sections, and for every document that is new or whose attachments
changed it downloads the attachments, reads their text (PDFs, signed ones included, spreadsheets,
word processor files, archives: see indexer/office.py) and indexes the document under the address of
its page (stable, with a fragment that tells the documents of a page apart). Unchanged documents are not downloaded again; documents no longer on a page read
without errors are removed.

Some sections list their documents through a search table filled by JavaScript (registers of
acts, grants, calls). The job asks the portal for the rows of the table (the same request the page
makes, all pages of it) and indexes every row as a short card: its description, the other columns
(number, date, office, type...) and the address of its detail page, without downloading anything.
Columns that hold personal identifiers (fiscal code, VAT number) are left out, as are the ones named
in "table_skip_columns". A table with at most "max_table_details" rows (default 300) is read in
full instead: the detail page of each row is parsed like any document page, attachments included.
Cards do not count against "max_documents" (nothing is downloaded for them), only against the time.

A section can name an older copy of the same documents ("replaces": regular expressions matched,
case-insensitively, against the decoded address of each indexed source): once the section has been
read in full and nothing is left for later, the passages of those sources are removed, once, so the
answers cite the live pages instead of the dead ones. `--show-replaced` lists them without removing.

Every passage carries origin=amt: the weekly and monthly syncs do not remove them as «stale».
Heavy work (downloads, embeddings) is spread over the nights: at most `max_documents` documents and
`max_minutes` minutes per run; what is left is picked up the next night.

Configuration: the "amt" key of config/crawl_extra.json
    {"portal": "https://example.nuvolapalitalsoft.it/",
     "page_id": 36046,                    # the transparency page of the portal
     "section": "amministrazione_trasparente",
     "max_attachments": 25,              # attachments read per document (default 25)
     "max_chars": 60000,                 # text kept per document (default 60000)
     "max_documents": 200,               # new or changed documents per run (default 200)
     "max_minutes": 150,                 # time per run (default 150)
     "max_table_details": 300,           # rows of a search table read in full, with attachments
     "table_skip_columns": ["Responsabile"],   # more columns left out of the cards (regexes)
     "sections": [{"voceamt": 56, "name": "Bilanci",
                   "max_chars": 3000000,          # a section can keep more (or less) text
                   "replaces": ["old\\.example\\.org/.*/bilanci/", "Rendiconto_della_gestione"]}]}
A section's own "max_chars" or "max_attachments" replaces the general one for its documents; when
they change, the documents of that section are read again at the next run.
Run (from cron through scripts/amt_sync.sh, which takes the lock of the syncs):
    venv/bin/python -m scripts.sync amt
    venv/bin/python -m scripts.amt_sync --dry-run [--section 56]   # walk and print, write nothing
    venv/bin/python -m scripts.amt_sync --show-replaced [--section 56]   # the older sources "replaces" matches
"""
import argparse
import hashlib
import html as htmllib
import json
import logging
import re
import sys
import tempfile
import time
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.privdrop import drop_to_sync_user

drop_to_sync_user()   # as root: become the sync account before config.settings reads .env

from config.settings import (
    AMT_SOURCES,
    CRAWL_ALLOWED_DOMAINS,
    CRAWL_CACHE_DIR,
    CRAWL_DELAY_SECONDS,
)
from crawler.netpolicy import get_limited
from indexer.chunker import chunk_document
from indexer.office import extract, kind

log = logging.getLogger(__name__)

STATE_FILE = CRAWL_CACHE_DIR / "amt_state.json"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; Zautte/1.0)"}
_DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")
_MAX_PAGES = 3000          # pages walked per run, whatever the tree looks like
_BATCH = 10                # documents indexed (and state saved) together
_CARD_BATCH = 200          # cards (rows of a table) indexed together: no download, short texts
_TABLE_PAGE = 500          # rows asked per request
_MAX_TABLE_ROWS = 200000   # rows read from one table, whatever the portal says
_PERSONAL_COLUMNS = re.compile(r"cod(?:ice)?\.?\s*fisc|p\.?\s*iva|partita\s*iva", re.IGNORECASE)
_TOKEN_RE = re.compile(r"ajax\.data\s*=\s*'([^']+)'")


# --------------------------------------------------------------------------- state

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            log.warning(f"Transparency state unreadable ({e}): starting from scratch")
    return {"items": {}, "replaced": []}


def save_state(state: dict) -> None:
    CRAWL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STATE_FILE)


# --------------------------------------------------------------------------- pages

def _voceamt(href: str) -> int | None:
    try:
        q = parse_qs(urlparse(htmllib.unescape(href)).query)
        return int(q["voceamt"][0]) if "voceamt" in q and "event" not in q else None
    except (ValueError, KeyError, IndexError):
        return None


def _iso(text: str) -> str:
    m = _DATE_RE.search(text or "")
    if not m:
        return ""
    try:
        return date(int(m[3]), int(m[2]), int(m[1])).isoformat()
    except ValueError:
        return ""


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return s[:80].rstrip("-") or "documento"


def parse_page(html: str, base: str) -> dict:
    """A transparency page: its title, the sub-sections (tiles), the documents (blocks with
    attachments) and whether it lists documents through a search table we do not read."""
    soup = BeautifulSoup(html, "lxml")
    content = soup.find("div", class_="page-content") or soup
    h1 = content.find("h1")
    children = []
    for a in content.find_all("a", class_="amt-tile-title"):
        v = _voceamt(a.get("href", ""))
        if v is not None:
            children.append((v, _clean(a.get_text(" "))))
    docs = []
    for box in content.find_all("div", class_="italsoft-alert"):
        h2 = box.find("h2")
        links = [a for a in box.find_all("a", href=True) if "event=vediAllegato" in a["href"] and _clean(a.get_text(" "))]
        if not h2 or not links:
            continue
        title = _clean(h2.get("title") or h2.get_text(" "))
        attachments = []
        for a in links:
            li = a.find_parent("li")
            fp = re.search(r"MD5:\s*([0-9a-f]{32})", li.get_text(" ") if li else "", re.IGNORECASE)
            attachments.append({"name": _clean(a.get_text(" ")), "url": urljoin(base, a["href"]),
                                "md5": fp[1].lower() if fp else ""})
        period = box.find(class_="data-ultimo-aggiornamento")
        for tag in box.find_all(["h2", "h4", "ul", "small"]):
            tag.extract()
        if period:
            period.extract()
        description = _clean(box.get_text(" "))
        docs.append({"title": title, "description": description, "attachments": attachments,
                     "published": _iso(period.get_text(" ") if period else "")})
    table = None
    grid = content.find("table", attrs={"data-ajax": True})
    token = _TOKEN_RE.search(html)
    if grid is not None and token:
        headers = [_clean(th.get_text(" ")) for th in grid.find_all("th")]
        table = {"headers": headers, "token": token[1]}
    return {"title": _clean(h1.get_text(" ")) if h1 else "", "children": children, "docs": docs,
            "lists": table is not None, "table": table}


# --------------------------------------------------------------------------- search tables

def _skip_column(header: str, config: dict) -> bool:
    if not header or _PERSONAL_COLUMNS.search(header):
        return True
    return any(re.search(p, header, re.IGNORECASE) for p in config.get("table_skip_columns", []) if p)


def parse_row(cells: list[str], headers: list[str], base: str, config: dict) -> dict | None:
    """A row of a search table as a card: its title (the first column), the detail page it links,
    the other columns as «Header: value» and the date, if a column holds one."""
    texts, detail = [], ""
    for i, cell in enumerate(cells):
        frag = BeautifulSoup(cell or "", "lxml")
        if i == 0:
            a = frag.find("a", href=True)
            if a is not None:
                detail = urljoin(base, htmllib.unescape(a["href"]))
        texts.append(_clean(frag.get_text(" ")))
    if not texts or not texts[0] or not detail or "dettaglio=" not in detail:
        return None
    fields, published = [], ""
    for header, value in zip(headers[1:], texts[1:], strict=False):
        if not value or _skip_column(header, config):
            continue
        fields.append(f"{header}: {value}")
        if not published and re.search(r"\bdata\b", header, re.IGNORECASE):
            published = _iso(value)
    return {"title": texts[0], "description": "; ".join(fields), "attachments": [], "published": published,
            "detail": detail}


def table_rows(post, config: dict, voceamt: int, table: dict, base: str) -> list[dict]:
    """Every row of a search table, page after page (the request the page itself makes)."""
    portal, page_id = config["portal"], int(config["page_id"])
    form = {"action": "itafrontoffice_ajax", "model": "oopp_portale_func", "data": table["token"],
            "event": "tablePager", "ajax": ""}
    rows: list[dict] = []
    page, total = 0, None
    while total is None or (len(rows) < total and len(rows) < _MAX_TABLE_ROWS):
        url = f"{portal}wp-admin/admin-ajax.php?page_id={page_id}&voceamt={voceamt}&page={page}&size={_TABLE_PAGE}"
        data = post(url, form)
        if not isinstance(data, list) or len(data) < 2 or not isinstance(data[1], list):
            raise ValueError("unexpected answer of the table")
        try:
            total = int(data[0])
        except (TypeError, ValueError):
            total = len(data[1])
        if not data[1]:
            break
        for cells in data[1]:
            row = parse_row(cells, table["headers"], base, config)
            if row:
                rows.append(row)
        page += 1
    return rows


_SECTION_LIMITS = ("max_chars", "max_attachments")


def section_limits(section: dict) -> dict:
    """The text limits a section sets for itself (none: the general ones apply)."""
    return {k: int(section[k]) for k in _SECTION_LIMITS if k in section}


def doc_digest(doc: dict, limits: dict | None = None) -> str:
    """Changes when the title, the description or an attachment changes (not with the tokens), and
    when the section's own text limits change, so its documents are read again with them. Documents
    with signed attachments (.p7m) carry a mark too: they were indexed without that text before the
    envelopes were opened (0.28.0), and the new digest reads them again once; the same for office
    files and archives (0.29.0)."""
    key = {"t": doc["title"], "d": doc["description"],
           "a": sorted(a["md5"] or a["name"] for a in doc["attachments"])}
    if limits:
        key["l"] = limits
    if any(a["name"].lower().endswith(".p7m") for a in doc["attachments"]):
        key["s"] = 1
    if any(kind(a["name"]) not in ("", "pdf") for a in doc["attachments"]):
        key["o"] = 1          # office files and archives: read since 0.29.0
    return hashlib.sha256(json.dumps(key, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def page_url(portal: str, page_id: int, voceamt: int) -> str:
    return f"{portal}?page_id={page_id}&voceamt={voceamt}"


def walk(fetch, config: dict, root: int, name: str, post=None, known: dict | None = None) -> tuple[list[dict], dict]:
    """All the documents under a section (breadth first). Returns (documents, counters); each
    document has its source, the path of sections and the page it is on. With `post`, the rows of
    the search tables come too: as cards (`card: True`), or as full documents for the small tables
    (`known`, the state, spares the detail pages of the rows already read and unchanged)."""
    portal, page_id = config["portal"], int(config["page_id"])
    queue: list[tuple[int, list[str]]] = [(root, [name] if name else [])]
    seen: set[int] = set()
    found: list[dict] = []
    counts = {"pages": 0, "failed": 0, "lists": 0, "rows": 0}
    while queue and counts["pages"] < _MAX_PAGES:
        voce, path = queue.pop(0)
        if voce in seen or voce == 0:
            continue
        seen.add(voce)
        url = page_url(portal, page_id, voce)
        try:
            page = parse_page(fetch(url), url)
        except (httpx.HTTPError, ValueError) as e:
            log.warning(f"  {url}: not read ({e})")
            counts["failed"] += 1
            continue
        counts["pages"] += 1
        here = path if (path and page["title"] == path[-1]) or not page["title"] else path + [page["title"]]
        if page["table"] is not None:
            counts["lists"] += 1
            if post is None:
                log.info(f"  {url}: a search table (not read in this pass)")
            else:
                try:
                    rows = table_rows(post, config, voce, page["table"], url)
                except (httpx.HTTPError, ValueError) as e:
                    log.warning(f"  {url}: table not read ({e})")
                    counts["failed"] += 1
                    rows = []
                full = len(rows) <= int(config.get("max_table_details", 300))
                counts["rows"] += len(rows)
                log.info(f"  {url}: a search table, {len(rows)} rows" + (" (read in full)" if full else " (cards)"))
                for row in rows:
                    detail = row.pop("detail")
                    doc = {**row, "source": detail, "page": detail, "path": here, "root": root, "card": not full}
                    if full:
                        doc = _detail_doc(fetch, doc, known) or doc
                    found.append(doc)
        used: dict[str, int] = {}
        for doc in page["docs"]:
            s = slug(doc["title"])
            used[s] = used.get(s, 0) + 1
            if used[s] > 1:
                s = f"{s}-{used[s]}"
            found.append({**doc, "source": f"{url}#{s}", "page": url, "path": here, "root": root})
        for child, title in page["children"]:
            if child not in seen:
                queue.append((child, here + [title] if title else here))
    if queue:
        log.warning(f"  section {root}: stopped after {_MAX_PAGES} pages")
        counts["failed"] += 1
    return found, counts


def _detail_doc(fetch, card: dict, known: dict | None) -> dict | None:
    """The document on the detail page of a row (title, description, attachments), read only when
    the row is new or changed; None when the page cannot be read or holds no document."""
    row_digest = doc_digest(card)
    was = (known or {}).get(card["source"], {})
    if was.get("row") == row_digest and was.get("doc"):
        return {**card, **was["doc"], "card": False, "row": row_digest, "cached": card}
    try:
        page = parse_page(fetch(card["source"]), card["source"])
    except (httpx.HTTPError, ValueError) as e:
        log.warning(f"  {card['source']}: detail not read ({e})")
        return None
    if not page["docs"]:
        return None
    doc = page["docs"][0]
    if card["description"] and card["description"] not in doc["description"]:
        doc["description"] = f"{card['description']}. {doc['description']}".strip(". ")
    return {**card, **doc, "card": False, "row": row_digest}


# --------------------------------------------------------------------------- documents

def _pdf_text(data: bytes, max_chars: int) -> str:
    """Text of a PDF, also when signed (`.pdf.p7m`: the PDF is taken out of the envelope); empty for
    anything else, or a PDF without text."""
    if not data.startswith(b"%PDF"):
        from indexer.p7m import unwrap
        data = unwrap(data) or b""
        if not data.startswith(b"%PDF"):
            return ""
    from indexer.pdf_extractor import extract_text_from_pdf
    CRAWL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".pdf", dir=CRAWL_CACHE_DIR, delete=True) as f:
        f.write(data)
        f.flush()
        return (extract_text_from_pdf(Path(f.name)) or "")[:max_chars]


def doc_text(doc: dict, site: str, texts: list[tuple[str, str]]) -> str:
    """The passage text: where the document is, what it is, its attachments, then their text."""
    where = f"{site}, Amministrazione trasparente" if site else "Amministrazione trasparente"
    parts = [f"{where}, sezione {' > '.join(doc['path'])}: {doc['title']}."]
    if doc["description"]:
        parts.append(doc["description"])
    names = "; ".join(a["name"] for a in doc["attachments"])
    if names:
        parts.append(f"Allegati: {names}.")
    if doc["published"]:
        d = date.fromisoformat(doc["published"])
        parts.append(f"Pubblicato dal {d:%d/%m/%Y}.")
    parts.append(f"Pagina del documento: {doc['page']}")
    head = "\n".join(parts)
    body = "\n\n".join(f"[{name}]\n{text}" for name, text in texts if text.strip())
    return head + ("\n\n" + body if body else "")


def build_doc(doc: dict, config: dict, texts: list[tuple[str, str]]) -> dict:
    from config.settings import SITE_NAME
    text = doc_text(doc, SITE_NAME, texts)
    extra = {"category": "documento", "section": config.get("section", "amministrazione_trasparente"),
             "origin": "amt"}
    if doc.get("card"):
        extra["card"] = True
    if doc["published"]:
        extra["date"] = doc["published"]
    chunks = chunk_document(text=text, source_url=doc["source"], title=doc["title"],
                            doc_type="pdf" if any(t.strip() for _, t in texts) else "html",
                            extra_metadata=extra)
    return {"url": doc["source"], "chunks": chunks}


def read_attachments(get_bytes, doc: dict, config: dict) -> list[tuple[str, str]]:
    """(name, text) of the attachments, up to the limits; an attachment that fails or cannot be read
    (an image, an old .doc) is skipped.
    The text kept for the document is shared among its attachments (the beginning of each, at
    least 4,000 characters): a long table of figures must not crowd out the report next to it."""
    attachments = doc["attachments"][:int(config.get("max_attachments", 25))]
    budget = int(config.get("max_chars", 60000))
    share = max(4000, budget // max(1, len(attachments)))
    texts = []
    for att in attachments:
        if budget <= 0:
            break
        try:
            text = extract(att["name"], get_bytes(att["url"]), min(share, budget), _pdf_text)
        except (httpx.HTTPError, ValueError, OSError) as e:
            log.warning(f"    {att['name']}: not downloaded ({e})")
            continue
        if kind(att["name"]) == "pdf":
            from indexer.pdf_extractor import record_pdf_quality
            record_pdf_quality(doc.get("source") or doc.get("page", ""), len(text),
                               document=doc.get("title", ""), attachment=att["name"])
        if text:
            texts.append((att["name"], text))
            budget -= len(text)
    return texts


# --------------------------------------------------------------------------- older copy

def replaced_sources(sources, patterns: list[str]) -> set[str]:
    """The indexed sources an older copy is made of: those whose decoded address matches a pattern
    (and that are not passages of this job)."""
    regexes = [re.compile(p, re.IGNORECASE) for p in patterns if p]
    if not regexes:
        return set()
    return {s for s in sources if "page_id=" not in s and any(r.search(unquote(s)) for r in regexes)}


# --------------------------------------------------------------------------- run

def _get(client: httpx.Client, url: str) -> httpx.Response:
    resp = get_limited(client, url, CRAWL_ALLOWED_DOMAINS)
    time.sleep(CRAWL_DELAY_SECONDS)
    resp.raise_for_status()
    return resp


def run_amt(dry_run: bool = False, only: int | None = None) -> dict:
    """One run over the configured sections. Returns the counters."""
    counts = {"documents": 0, "new": 0, "changed": 0, "removed": 0, "deferred": 0, "failed": 0,
              "lists": 0, "rows": 0, "cards": 0, "replaced": 0, "chunks": 0}
    config = AMT_SOURCES
    if not config.get("portal") or not config.get("page_id") or not config.get("sections"):
        log.warning("No transparency section configured (crawl_extra.json → \"amt\")")
        return counts
    config = {**config, "portal": config["portal"].rstrip("/") + "/"}
    sections = [s for s in config["sections"] if only is None or int(s["voceamt"]) == only]
    max_docs = int(config.get("max_documents", 200))
    deadline = time.monotonic() + 60 * int(config.get("max_minutes", 150))
    state = load_state()
    items = state.setdefault("items", {})
    replaced = set(state.setdefault("replaced", []))

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

        for section in sections:
            root = int(section["voceamt"])
            limits = section_limits(section)
            sconfig = {**config, **limits}
            log.info(f"Section {root} ({section.get('name', '')})" + (f", limits {limits}" if limits else ""))
            docs, walked = walk(fetch, config, root, section.get("name", ""), post=post, known=items)
            counts["documents"] += len(docs)
            counts["failed"] += walked["failed"]
            counts["lists"] += walked["lists"]
            counts["rows"] += walked["rows"]
            todo = [d for d in docs if items.get(d["source"], {}).get("digest") != doc_digest(d, limits)]
            # a cached detail (unchanged row) that must be indexed again: fresh attachment links
            todo = [(_detail_doc(fetch, d["cached"], None) or d) if d.get("cached") else d for d in todo]
            gone = set() if walked["failed"] else (
                {s for s, it in items.items() if it.get("root") == root} - {d["source"] for d in docs})
            log.info(f"  {walked['pages']} pages, {len(docs)} documents ({walked['rows']} rows of tables), "
                     f"{len(todo)} new or changed, {len(gone)} gone")
            if dry_run:
                for d in todo[:10]:
                    print(f"- {' > '.join(d['path'])}: {d['title']} ({len(d['attachments'])} allegati) {d['source']}")
                continue

            from indexer.indexer import _index_documents
            from indexer.vector_store import get_indexed_sources, remove_sources

            def remember(d, limits=limits, root=root):
                it = {"digest": doc_digest(d, limits), "root": root, "title": d["title"]}
                if d.get("row"):          # a row read in full: its detail page is not fetched again
                    it["row"] = d["row"]
                    it["doc"] = {k: d[k] for k in ("title", "description", "attachments", "published")}
                items[d["source"]] = it

            cards = [d for d in todo if d.get("card")]
            pending = [d for d in todo if not d.get("card")]
            while cards:
                if time.monotonic() > deadline:
                    counts["deferred"] += len(cards)
                    log.info(f"  {len(cards)} cards left for the next run")
                    break
                batch, cards = cards[:_CARD_BATCH], cards[_CARD_BATCH:]
                log.info(f"  {len(batch)} cards ({len(cards)} to go)")
                built = [build_doc(d, sconfig, []) for d in batch]
                _, inserted = _index_documents(built, "AMT", lambda b: b["chunks"], skip_existing=False)
                counts["chunks"] += inserted
                for d in batch:
                    counts["cards"] += 1
                    remember(d)
                save_state(state)
            while pending:
                if counts["new"] + counts["changed"] >= max_docs or time.monotonic() > deadline:
                    counts["deferred"] += len(pending)
                    log.info(f"  {len(pending)} documents left for the next run")
                    break
                batch, pending = pending[:_BATCH], pending[_BATCH:]
                built = []
                for d in batch:
                    log.info(f"  {d['title'][:90]} ({len(d['attachments'])} attachments)")
                    built.append(build_doc(d, sconfig, read_attachments(get_bytes, d, sconfig)))
                _, inserted = _index_documents(built, "AMT", lambda b: b["chunks"], skip_existing=False)
                counts["chunks"] += inserted
                for d in batch:
                    counts["changed" if d["source"] in items else "new"] += 1
                    remember(d)
                save_state(state)
            if gone:
                remove_sources(gone, keep_inbox=False)
                for s in gone:
                    items.pop(s, None)
                counts["removed"] += len(gone)
                save_state(state)
            # the older copy goes only when this section is complete: read in full, nothing left
            patterns = section.get("replaces", [])
            if patterns and not walked["failed"] and not pending and not cards and docs and root not in replaced:
                old = replaced_sources(get_indexed_sources(), patterns)
                if old:
                    remove_sources(old, keep_inbox=False)
                log.info(f"  older copy: {len(old)} sources removed")
                counts["replaced"] += len(old)
                replaced.add(root)
                state["replaced"] = sorted(replaced)
                save_state(state)
    log.info(f"Transparency: {counts['documents']} documents, {counts['new']} new, {counts['changed']} changed, "
             f"{counts['cards']} cards, {counts['removed']} removed, {counts['deferred']} left for later, "
             f"{counts['replaced']} older sources replaced, {counts['lists']} search tables ({counts['rows']} rows), "
             f"{counts['failed']} pages not read")
    return counts


def show_replaced(only: int | None = None) -> int:
    """Prints, per section, the indexed sources its "replaces" patterns match (nothing is removed)."""
    from indexer.vector_store import _ensure_loaded, get_indexed_sources
    _ensure_loaded()
    from indexer import vector_store
    per_source: dict[str, int] = {}
    for m in vector_store._metadata:
        per_source[m.get("source", "")] = per_source.get(m.get("source", ""), 0) + 1
    sources = get_indexed_sources()
    for section in AMT_SOURCES.get("sections", []):
        if only is not None and int(section["voceamt"]) != only:
            continue
        old = sorted(replaced_sources(sources, section.get("replaces", [])))
        print(f"# section {section['voceamt']} ({section.get('name', '')}): {len(old)} sources, "
              f"{sum(per_source.get(s, 0) for s in old)} passages")
        for s in old:
            print(f"{per_source.get(s, 0)}\t{unquote(s)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Amministrazione trasparente on a Nuvola portal → passages")
    ap.add_argument("--dry-run", action="store_true", help="walk the sections and print, write nothing")
    ap.add_argument("--section", type=int, help="only this configured section (its voceamt)")
    ap.add_argument("--show-replaced", action="store_true",
                    help="list the indexed sources the \"replaces\" patterns match, remove nothing")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    if args.show_replaced:
        return show_replaced(args.section)
    counts = run_amt(dry_run=args.dry_run, only=args.section)
    return 1 if counts["failed"] and not counts["documents"] else 0


if __name__ == "__main__":
    sys.exit(main())
