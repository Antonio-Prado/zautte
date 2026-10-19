"""
Notizie quasi in tempo reale.

Il crawl settimanale (lunedì notte) lascia fuori per giorni le notizie appena
pubblicate. Questo modulo legge le fonti configurate in `config/crawl_extra.json`
(chiave "news"):

  - un sito di notizie WordPress, per esempio un bollettino dell'ente
    (`wordpress_api`): articoli completi tramite la REST API `wp/v2/posts`;
  - i feed RSS del sito istituzionale (`rss`): titolo, link e data.

Per ogni notizia nuova o modificata indicizza:
  1. l'articolo del Bollettino;
  2. le pagine del sito istituzionale linkate nell'articolo (la notizia gemella,
     un servizio): stesso testo e stessi metadati del crawler, così quando il
     crawl le raggiunge trova i chunk già presenti;
  3. gli allegati PDF, ODT e DOCX citati dall'articolo o dalla pagina gemella
     (bando, modulo, avviso), scaricati in data/documents come fa il crawler.

Tutti i chunk portano origin=news: i sync settimanali e mensili non li tolgono
come «stale» (il Bollettino e le notizie vecchie non sono raggiunti dal crawl),
ma restano sostituibili quando il crawler o questo modulo reindicizzano la
stessa sorgente (vedi vector_store._is_protected e source_chunk_ids).

Lo stato (`data/crawl_cache/news_state.json`) ricorda l'ultima modifica vista e
l'hash di ogni notizia: una corsa senza novità costa due richieste HTTP e non
carica il vector store. Uso: `python -m scripts.sync news [--backfill]`
(--backfill = tutto l'archivio del Bollettino, da usare una volta).
"""
import hashlib
import json
import logging
import re
import sys
import time
import zipfile
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import (
    CRAWL_ALLOWED_DOMAINS,
    CRAWL_CACHE_DIR,
    CRAWL_DELAY_SECONDS,
    CRAWL_PDF_EXCLUDE_PATTERNS,
    CRAWL_URL_ALIASES,
    DOCUMENTS_DIR,
    NEWS_SOURCES,
    now_local,
)
from crawler.crawler import (
    PDF_MAGIC,
    canonical_url,
    clean_text,
    extract_metadata,
    extract_title,
    save_link_texts,
    should_skip,
    url_to_filename,
)
from indexer.chunker import chunk_document
from indexer.indexer import _index_documents
from indexer.pdf_extractor import extract_text_from_pdf, get_pdf_metadata

log = logging.getLogger(__name__)

STATE_FILE = CRAWL_CACHE_DIR / "news_state.json"
ATTACHMENT_EXT = (".pdf", ".odt", ".docx")
MIN_TEXT_LEN = 100
MAX_LINKED_PAGES = 10       # pagine del sito seguite per articolo
FIRST_RUN_DAYS = 30         # senza stato e senza --backfill: ultimi N giorni
WP_PER_PAGE = 100
MONTHS_IT = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio",
             "agosto", "settembre", "ottobre", "novembre", "dicembre"]
_BLOCK_TAGS = ["p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "figcaption", "td", "pre"]
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; Zautte/1.0)"}


# --------------------------------------------------------------------------- stato

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            log.warning(f"Stato notizie illeggibile ({e}): riparto da zero")
    return {"wp_cursor": "", "items": {}}


def save_state(state: dict) -> None:
    CRAWL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STATE_FILE)


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8", errors="replace")).hexdigest()[:16]


# --------------------------------------------------------------------------- fonti

def _get(client: httpx.Client, url: str, **kw) -> httpx.Response | None:
    try:
        resp = client.get(url, **kw)
    except (httpx.HTTPError, httpx.InvalidURL, ValueError) as e:
        # InvalidURL/ValueError (anche IDNAError): link malformati negli articoli,
        # es. un host più lungo di 253 caratteri (archivio 2025, 12/10/2026)
        log.warning(f"  Errore di rete: {url[:120]} — {e}")
        return None
    time.sleep(CRAWL_DELAY_SECONDS)
    if resp.status_code >= 400:
        log.warning(f"  HTTP {resp.status_code}: {url}")
        return None
    return resp


def wp_categories(client: httpx.Client, api: str) -> dict[int, str]:
    resp = _get(client, f"{api}/categories", params={"per_page": 100, "_fields": "id,name"})
    if resp is None:
        return {}
    try:
        return {c["id"]: BeautifulSoup(c["name"], "lxml").get_text() for c in resp.json()}
    except (ValueError, KeyError, TypeError):
        return {}


def wp_posts(client: httpx.Client, api: str, modified_after: str = "", after: str = "") -> list[dict]:
    """Articoli del WordPress, dal più vecchio: tutti (backfill), quelli modificati
    dopo `modified_after` oppure pubblicati dopo `after` (ISO 8601, ora locale)."""
    params = {"per_page": WP_PER_PAGE, "orderby": "modified" if modified_after else "date",
              "order": "asc", "status": "publish",
              "_fields": "id,date,modified,link,title,content,categories"}
    if modified_after:
        params["modified_after"] = modified_after
    elif after:
        params["after"] = after
    posts: list[dict] = []
    page = 1
    while True:
        resp = _get(client, f"{api}/posts", params={**params, "page": page})
        if resp is None:
            break
        try:
            batch = resp.json()
        except ValueError:
            log.warning("  Risposta WordPress non JSON")
            break
        if not isinstance(batch, list) or not batch:
            break
        for p in batch:
            try:
                posts.append({
                    "url": p["link"],
                    "title": BeautifulSoup(p["title"]["rendered"], "lxml").get_text(" ", strip=True),
                    "date": p["date"][:10],
                    "modified": p["modified"][:19],
                    "html": p["content"]["rendered"],
                    "categories": p.get("categories", []),
                })
            except (KeyError, TypeError):
                continue
        total_pages = int(resp.headers.get("x-wp-totalpages", "1") or 1)
        if page >= total_pages or len(batch) < WP_PER_PAGE:
            break
        page += 1
    return posts


def rss_items(client: httpx.Client, url: str) -> list[dict]:
    resp = _get(client, url)
    if resp is None:
        return []
    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as e:
        log.warning(f"  Feed non leggibile {url}: {e}")
        return []
    items = []
    for it in root.iter("item"):
        link = (it.findtext("link") or "").strip()
        if not link:
            continue
        pub = (it.findtext("pubDate") or "").strip()
        try:
            pub_date = parsedate_to_datetime(pub).strftime("%Y-%m-%d") if pub else ""
        except (ValueError, TypeError):
            pub_date = ""
        items.append({"url": canonical_url(link), "title": (it.findtext("title") or "").strip(),
                      "date": pub_date})
    return items


# --------------------------------------------------------------------------- testo

def html_to_text(html: str) -> str:
    """Testo dell'articolo a paragrafi (un blocco per p/h/li/td), senza i widget
    di condivisione e gli articoli correlati che WordPress accoda al contenuto."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(["script", "style", "iframe", "noscript", "form"]):
        tag.decompose()
    for tag in soup.find_all(class_=re.compile(r"share|sharedaddy|related|jp-|social|wp-block-embed", re.IGNORECASE)):
        tag.decompose()
    blocks = []
    for tag in soup.find_all(_BLOCK_TAGS):
        if tag.find_parent(_BLOCK_TAGS):
            continue
        text = tag.get_text(" ", strip=True)
        if text:
            blocks.append(text)
    if not blocks:
        blocks = [ln.strip() for ln in soup.get_text("\n").splitlines() if ln.strip()]
    return "\n\n".join(blocks).strip()


def date_it(iso: str) -> str:
    try:
        d = date.fromisoformat(iso[:10])
        return f"{d.day} {MONTHS_IT[d.month - 1]} {d.year}"
    except ValueError:
        return iso


def office_text(path: Path) -> str:
    """Testo di un ODT o DOCX (paragrafi) leggendo l'XML nel pacchetto zip."""
    ext = path.suffix.lower()
    inner = "content.xml" if ext == ".odt" else "word/document.xml"
    if ext == ".odt":
        ns = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
        tags = {ns + "p", ns + "h"}
    else:
        tags = {"{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"}
    try:
        with zipfile.ZipFile(path) as z:
            root = ET.fromstring(z.read(inner))
    except (zipfile.BadZipFile, KeyError, ET.ParseError, OSError) as e:
        log.warning(f"  Documento non leggibile {path.name}: {e}")
        return ""
    paras = ["".join(el.itertext()).strip() for el in root.iter() if el.tag in tags]
    return "\n\n".join(p for p in paras if p)


# --------------------------------------------------------------------------- link

def _attachment_hosts() -> set[str]:
    hosts = set(CRAWL_ALLOWED_DOMAINS) | set(NEWS_SOURCES.get("attachment_hosts", []))
    for alias in CRAWL_URL_ALIASES:
        hosts.update(alias.get("hosts", []))
    return hosts


def _host_ok(url: str, hosts: set[str]) -> bool:
    netloc = urlparse(url).netloc.lower()
    return any(h in netloc for h in hosts)


def find_links(html: str, base_url: str) -> tuple[list[str], list[tuple[str, str]]]:
    """(pagine del sito da seguire, allegati (url, testo del link)) citati nell'HTML."""
    soup = BeautifulSoup(html, "lxml")
    hosts = _attachment_hosts()
    pages: list[str] = []
    attachments: list[tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        url = canonical_url(urljoin(base_url, href)).split("#", 1)[0]
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc or len(parsed.netloc) > 253:
            continue   # link malformato (host assente o troppo lungo): httpx lo rifiuterebbe
        path = parsed.path.lower()
        if path.endswith(ATTACHMENT_EXT):
            if (_host_ok(url, hosts) and url not in [u for u, _ in attachments]
                    and not any(pat in url for pat in CRAWL_PDF_EXCLUDE_PATTERNS)):
                attachments.append((url, a.get_text(" ", strip=True) or (a.get("title") or "").strip()))
        elif not should_skip(url) and url not in pages and url.rstrip("/") != base_url.rstrip("/"):
            pages.append(url)
    return pages[:MAX_LINKED_PAGES], attachments


# --------------------------------------------------------------------------- documenti

def _doc(url: str, chunks: list[dict]) -> dict:
    return {"url": url, "chunks": chunks}


def post_doc(post: dict, categories: dict[int, str]) -> dict | None:
    body = html_to_text(post["html"])
    if len(body) < MIN_TEXT_LEN:
        return None
    # Categorie redazionali (Scuola, Tributi...), non quelle tecniche di impaginazione
    # («1 - COPERTINA», «Evidenza»)
    cats = ", ".join(n for n in (categories.get(c, "") for c in post["categories"])
                     if n and not re.match(r"^\d|copertina|evidenza", n, re.IGNORECASE))
    head = f"Notizia del {date_it(post['date'])}"
    if cats:
        head += f" ({cats})"
    # La data va nello stesso paragrafo del primo blocco: il chunker scarta i
    # paragrafi sotto gli 80 caratteri e ne fa un chunk per paragrafo.
    chunks = chunk_document(
        text=f"{head}. {body}", source_url=post["url"], title=post["title"], doc_type="html",
        extra_metadata={"category": "notizia", "section": NEWS_SOURCES.get("section", "bollettino"),
                        "date": post["date"], "origin": "news"},
    )
    return _doc(post["url"], chunks) if chunks else None


def page_doc(client: httpx.Client, url: str, date: str) -> tuple[dict | None, str]:
    """Pagina del sito istituzionale: stesso trattamento del crawler. Ritorna
    (documento, HTML) — l'HTML serve a cercare gli allegati."""
    resp = _get(client, url)
    if resp is None or "text/html" not in resp.headers.get("content-type", ""):
        return None, ""
    html = resp.text
    text = clean_text(html)
    if len(text) < MIN_TEXT_LEN:
        return None, html
    meta = extract_metadata(html, url)
    if date and not meta.get("date"):
        meta["date"] = date
    meta["origin"] = "news"
    chunks = chunk_document(text=text, source_url=url, title=extract_title(html),
                            doc_type="html", extra_metadata=meta)
    return (_doc(url, chunks) if chunks else None), html


def attachment_doc(client: httpx.Client, url: str, link_text: str, date: str) -> dict | None:
    resp = _get(client, url)
    if resp is None:
        return None
    ext = Path(urlparse(url).path).suffix.lower()
    if ext == ".pdf" and not resp.content.startswith(PDF_MAGIC):
        log.warning(f"  Non è un PDF: {url}")
        return None
    DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
    fpath = DOCUMENTS_DIR / (url_to_filename(url) + ext)
    fpath.write_bytes(resp.content)
    extra: dict = {"category": "allegato", "date": date}
    if ext == ".pdf":
        text = extract_text_from_pdf(fpath)
        meta = get_pdf_metadata(fpath)
        title = meta["title"] or link_text or fpath.stem
        extra["pdf_pages"] = meta["pages"]
        doc_type = "pdf"
    else:
        text = office_text(fpath)
        title = link_text or fpath.stem
        doc_type = "document"
    if len(text.strip()) < MIN_TEXT_LEN:
        log.info(f"  Allegato senza testo utile (scansione?): {fpath.name}")
        return None
    extra["origin"] = "news"
    chunks = chunk_document(text=text, source_url=url, title=title, doc_type=doc_type,
                            extra_metadata=extra)
    return _doc(url, chunks) if chunks else None


# --------------------------------------------------------------------------- corsa

def collect(client: httpx.Client, state: dict, backfill: bool) -> tuple[list[dict], dict, dict]:
    """Scarica le novità. Ritorna (documenti da indicizzare, nuovo stato, contatori)."""
    items: dict = state.get("items", {})
    new_items: dict = {}
    docs: list[dict] = []
    seen_urls: set[str] = set()
    counts = {"posts": 0, "pages": 0, "attachments": 0}
    link_texts: dict[str, list[str]] = {}
    wp_cursor = state.get("wp_cursor", "")

    def add(doc: dict | None, kind: str) -> None:
        if doc and doc["url"] not in seen_urls:
            seen_urls.add(doc["url"])
            docs.append(doc)
            counts[kind] += 1

    def follow(html: str, base_url: str, date: str, pages: bool) -> None:
        linked, attachments = find_links(html, base_url)
        if pages:
            for url in linked:
                if url in seen_urls:
                    continue
                doc, page_html = page_doc(client, url, date)
                add(doc, "pages")
                if page_html:
                    follow(page_html, url, date, pages=False)
        for url, text in attachments:
            if url in seen_urls:
                continue
            if text:
                link_texts.setdefault(url, []).append(text)
            add(attachment_doc(client, url, text, date), "attachments")

    api = NEWS_SOURCES.get("wordpress_api", "")
    if api:
        if backfill:
            posts = wp_posts(client, api)
        elif wp_cursor:
            margin = (datetime.fromisoformat(wp_cursor) - timedelta(hours=1)).isoformat(timespec="seconds")
            posts = wp_posts(client, api, modified_after=margin)
        else:
            since = (now_local() - timedelta(days=FIRST_RUN_DAYS)).replace(tzinfo=None)
            posts = wp_posts(client, api, after=since.isoformat(timespec="seconds"))
        categories = wp_categories(client, api) if posts else {}
        for post in posts:
            wp_cursor = max(wp_cursor, post["modified"])
            # Hash sul testo pulito, non sull'HTML: il contenuto reso da WordPress
            # cambia a ogni richiesta (articoli correlati, condivisione) e con l'HTML
            # l'ultimo articolo veniva reindicizzato a ogni corsa (12/10/2026).
            digest = _digest(post["title"], html_to_text(post["html"]), post["date"])
            if items.get(post["url"], {}).get("hash") == digest:
                continue
            doc = post_doc(post, categories)
            if doc is None:
                continue
            log.info(f"  NUOVA [bollettino {post['date']}]: '{post['title']}'")
            add(doc, "posts")
            new_items[post["url"]] = {"hash": digest, "ts": now_local().isoformat(timespec="seconds")}
            try:
                follow(post["html"], post["url"], post["date"], pages=True)
            except (httpx.HTTPError, httpx.InvalidURL, ValueError, OSError) as e:
                # un link o un allegato guasto non deve fermare la corsa (l'articolo è già in coda)
                log.warning(f"  Link dell'articolo non seguiti: {post['url']} — {e}")

    for feed in NEWS_SOURCES.get("rss", []):
        for item in rss_items(client, feed):
            if item["url"] in seen_urls or item["url"] in items:
                continue
            doc, html = page_doc(client, item["url"], item["date"])
            if doc is None:
                continue
            log.info(f"  NUOVA [sito {item['date']}]: '{item['title']}'")
            add(doc, "pages")
            new_items[item["url"]] = {"hash": _digest(item["title"], item["date"]),
                                      "ts": now_local().isoformat(timespec="seconds")}
            try:
                follow(html, item["url"], item["date"], pages=False)
            except (httpx.HTTPError, httpx.InvalidURL, ValueError, OSError) as e:
                log.warning(f"  Allegati della notizia non seguiti: {item['url']} — {e}")

    if link_texts:
        save_link_texts(link_texts)
    new_state = {"wp_cursor": wp_cursor, "items": {**items, **new_items},
                 "last_run": now_local().isoformat(timespec="seconds")}
    return docs, new_state, counts


def run_news(backfill: bool = False) -> dict:
    """Una corsa: novità → indice. Ritorna i contatori (0 se niente di nuovo)."""
    if not NEWS_SOURCES:
        log.warning("Nessuna fonte notizie configurata (crawl_extra.json → \"news\")")
        return {"posts": 0, "pages": 0, "attachments": 0, "chunks": 0}
    state = load_state()
    if backfill:
        state = {"wp_cursor": "", "items": {}}
    with httpx.Client(headers=_HEADERS, follow_redirects=True, timeout=30.0) as client:
        docs, new_state, counts = collect(client, state, backfill)
    if not docs:
        log.info("Notizie: nessuna novità")
        save_state(new_state)
        return {**counts, "chunks": 0}
    log.info(f"Notizie: {counts['posts']} articoli, {counts['pages']} pagine, "
             f"{counts['attachments']} allegati da indicizzare")
    _, inserted = _index_documents(docs, "NEWS", lambda d: d["chunks"], skip_existing=False)
    save_state(new_state)
    return {**counts, "chunks": inserted}
