"""
Asynchronous crawler for static websites and CMSs.
Downloads HTML pages and PDF documents, saving the results in data/crawl_cache/.

Supports two modes:
  - Full (default): rescans the whole site
  - Incremental (--incremental): downloads only pages changed since the last scan
"""

import argparse
import asyncio
import hashlib
import json
import logging
import re
import sys
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import (
    CRAWL_ALLOWED_DOMAINS,
    CRAWL_CACHE_DIR,
    CRAWL_DELAY_SECONDS,
    CRAWL_DOMAIN_MAX_PATH_DEPTH,
    CRAWL_EXCLUDE_PATTERNS,
    CRAWL_EXTRA_START_URLS,
    CRAWL_MAX_PAGES,
    CRAWL_MAX_PATH_DEPTH,
    CRAWL_MAX_QUERY_VARIANTS,
    CRAWL_PDF_EXCLUDE_PATTERNS,
    CRAWL_SECTION_RULES,
    CRAWL_URL_ALIASES,
    DOCUMENTS_DIR,
    SECTION_DEFAULT,
    SITE_URL,
)
from crawler.netpolicy import get_limited_async, guard_async, url_allowed
from crawler.state import CrawlState, content_hash, write_json_atomic
from indexer.titles import LINK_TEXTS_FILE, best_link_text

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)


def url_to_filename(url: str) -> str:
    """Cache file name of a URL: the start of its path and 96 bits of its SHA-256 (until 0.7.23 it
    was 48 bits of MD5: the files saved that way are replaced when the URL is saved again)."""
    h = hashlib.sha256(url.encode()).hexdigest()[:24]
    path = urlparse(url).path.strip("/").replace("/", "_")[:60]
    return f"{path}_{h}" if path else h


def legacy_filename(url: str) -> str:
    """The name url_to_filename gave before 0.7.23 (48 bits of MD5)."""
    h = hashlib.md5(url.encode()).hexdigest()[:12]
    path = urlparse(url).path.strip("/").replace("/", "_")[:60]
    return f"{path}_{h}" if path else h


PDF_MAGIC = b"%PDF"


def _is_pdf_file(path: Path) -> bool:
    """True if the file exists and starts with the PDF header."""
    try:
        with open(path, "rb") as f:
            return f.read(4) == PDF_MAGIC
    except OSError:
        return False


def _pdf_response_ok(resp: httpx.Response) -> bool:
    """A response is a PDF only if it is 200 and the body starts with %PDF: servers
    may answer 200 with an HTML error page, which used to be saved
    as .pdf and made extraction fail on every sync."""
    return resp.status_code == 200 and resp.content[:4] == PDF_MAGIC


def _describe(resp: httpx.Response) -> str:
    """Summary of an anomalous response for the logs: status, content-type, text."""
    ct = resp.headers.get("content-type", "?")
    snippet = re.sub(r"<[^>]+>", " ", resp.text[:400])
    snippet = re.sub(r"\s+", " ", snippet).strip()[:80]
    return f"HTTP {resp.status_code}, {ct}, '{snippet}'"


async def _get(client: httpx.AsyncClient, url: str) -> httpx.Response:
    """GET without cookies. The httpx client keeps cookies between requests; on
    a tourism subdomain the WordPress Post Views Counter plugin adds every
    visited post to the pvc_visits cookie, and after ~1,650 pages the header exceeds
    the Apache limit (8 KB): from then on every request to the site, PDFs included,
    got 400 Bad Request. No crawled page requires cookies."""
    client.cookies.clear()
    return await get_limited_async(client, url, CRAWL_ALLOWED_DOMAINS)


def should_skip(url: str) -> bool:
    parsed = urlparse(url)

    # Non-HTTP protocols (webcal, ftp, etc.)
    if parsed.scheme not in ("http", "https"):
        return True

    # Protocol, credentials in the address (e.g. http://utente@dominio.it), IP literals,
    # ports and domain: one policy for pages, PDFs and the news sync (crawler/netpolicy.py)
    if not url_allowed(url, CRAWL_ALLOWED_DOMAINS):
        return True

    # Excluded patterns (extensions, forbidden paths, PHP scripts, etc.)
    full = parsed.path + ("?" + parsed.query if parsed.query else "")
    if any(pat in full for pat in CRAWL_EXCLUDE_PATTERNS):
        return True

    # Maximum path depth: prevents explosion on sites with
    # cumulative navigation.
    # The per-domain limit (stricter) takes priority over the global one.
    segments = [s for s in parsed.path.split("/") if s]
    domain_key = next(
        (k for k in CRAWL_DOMAIN_MAX_PATH_DEPTH if k in parsed.netloc), None
    )
    max_depth = CRAWL_DOMAIN_MAX_PATH_DEPTH[domain_key] if domain_key else CRAWL_MAX_PATH_DEPTH
    if len(segments) > max_depth:
        return True

    # Loop detector: if a non-numeric segment appears more than once in the path,
    # the CMS is likely accumulating breadcrumbs → discard the URL.
    non_numeric = [s for s in segments if not s.isdigit()]
    return len(non_numeric) != len(set(non_numeric))


def canonical_url(url: str) -> str:
    """Rewrite URLs of alias hosts (CRAWL_URL_ALIASES) onto the canonical host: the same
    attachment served by the organisation's site, an external portal and an S3 bucket becomes a
    single URL, so it is downloaded and indexed only once."""
    if not CRAWL_URL_ALIASES:
        return url
    parsed = urlparse(url)
    for alias in CRAWL_URL_ALIASES:
        if (parsed.netloc in alias.get("hosts", [])
                and parsed.path.startswith(alias.get("path_prefix", "/"))):
            return parsed._replace(scheme="https", netloc=alias["canonical_host"]).geturl()
    return url


def extract_links(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    links = []
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if href.startswith(("mailto:", "tel:", "webcal:", "javascript:", "#")):
            continue
        absolute = urljoin(base_url, href)
        absolute, _ = urldefrag(absolute)
        absolute = canonical_url(absolute)
        if not should_skip(absolute):
            links.append(absolute)
    return links


def extract_pdf_links(html: str, base_url: str) -> list[tuple[str, str]]:
    """Links to PDFs on allowed domains, except those in CRAWL_PDF_EXCLUDE_PATTERNS:
    (URL, link text). The text is used to give the PDF a readable title; without
    text (image link) the title attribute is tried."""
    soup = BeautifulSoup(html, "lxml")
    pdfs = []
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if href.lower().endswith(".pdf"):
            absolute = canonical_url(urljoin(base_url, href))
            if (url_allowed(absolute, CRAWL_ALLOWED_DOMAINS)
                    and not any(pat in absolute for pat in CRAWL_PDF_EXCLUDE_PATTERNS)):
                text = tag.get_text(" ", strip=True) or (tag.get("title") or "").strip()
                pdfs.append((absolute, text))
    return pdfs


def save_link_texts(observed: dict[str, list[str]]) -> int:
    """Update data/crawl_cache/pdf_link_texts.json (PDF URL → most descriptive link
    text). PDFs not seen in this crawl keep their previous text."""
    path = CRAWL_CACHE_DIR / LINK_TEXTS_FILE
    try:
        saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        log.warning(f"{path} unreadable: rewriting it from scratch")
        saved = {}
    updated = 0
    for url, texts in observed.items():
        best = best_link_text(texts)
        if best and saved.get(url) != best:
            saved[url] = best
            updated += 1
    write_json_atomic(path, saved, indent=1)
    return updated


# Inline formatting must not break a sentence into lines: get_text() with a separator puts
# the text of <strong>Non</strong> on a line of its own, and the line filter below drops it as
# noise (shorter than 4 characters), so a negation disappeared from the index. These tags are
# unwrapped before the extraction, so their text stays in the sentence.
_INLINE_TAGS = ("a", "abbr", "b", "em", "i", "mark", "small", "span", "strong", "sub", "sup", "u")


_NOISE_LINES = re.compile(
    r"^(vai alla pagina|leggi di più|leggi di piu|torna ai contenuti|"
    r"torna all'inizio|condividi|stampa|home|cerca|ricerca|accedi|"
    r"seguici su|newsletter|cookie|privacy policy|note legali|"
    r"tutti i servizi|tutti i documenti|tutte le news|"
    r"le indicazioni erano|ho avuto problemi|a volte le indicazioni|"
    r"capivo sempre|non ho avuto problemi|dove hai incontrato|"
    r"quali sono stati|altro|\d+/\d+)$",
    re.IGNORECASE
)


# Text that a visitor cannot see but the extraction would read: it could carry instructions
# for the model (indirect prompt injection). aria-hidden is not here on purpose: it hides
# a decorative element from screen readers, not from the eye.
_INVISIBLE_STYLE = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*(?:0(?![.\d])|[12]px)|opacity\s*:\s*0(?![.\d])|"
    r"(?:left|top|text-indent)\s*:\s*-\d{3,}|clip(?:-path)?\s*:\s*(?:rect\(\s*0|inset\(\s*50%)|"
    r"(?:height|width)\s*:\s*0(?![.\d])[^\"]*overflow\s*:\s*hidden",
    re.IGNORECASE,
)
# Classes of the common frameworks that hide an element from the eye (screen-reader-only text included:
# it is navigation noise for the index, and as good a place as any to hide instructions)
_INVISIBLE_CLASS = re.compile(r"^(?:d-none|sr-only|visually-hidden|screen-reader-text|u-hidden|hidden)$", re.IGNORECASE)


def drop_invisible(soup: BeautifulSoup) -> None:
    for tag in soup.find_all(True):
        if tag.decomposed:
            continue   # inside an element that was already removed
        classes = tag.get("class") or []
        if (tag.has_attr("hidden") or _INVISIBLE_STYLE.search(tag.get("style") or "")
                or any(_INVISIBLE_CLASS.match(c) for c in classes)):
            tag.decompose()


def _variant_ok(link: str, variants: dict[tuple[str, str], int]) -> bool:
    """A calendar or a filter makes endless URLs that differ only in the query string: past
    CRAWL_MAX_QUERY_VARIANTS of one path the others are not followed."""
    parts = urlparse(link)
    if not parts.query:
        return True
    key = (parts.netloc, parts.path)
    variants[key] = variants.get(key, 0) + 1
    return variants[key] <= CRAWL_MAX_QUERY_VARIANTS


def clean_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    drop_invisible(soup)

    # Remove useless tags
    for tag in soup.find_all(["script", "style", "nav", "header", "footer",
                               "aside", "noscript", "iframe", "form"]):
        tag.decompose()

    # Remove feedback/rating widgets
    for tag in soup.find_all(class_=re.compile(
        r"feedback|rating|survey|cookie|breadcrumb|pagination|share|social|"
        r"widget|banner|alert|modal|tooltip|dropdown|collapse", re.IGNORECASE
    )):
        tag.decompose()

    main = (
        soup.find("main") or
        soup.find(id=re.compile(r"content|main|body", re.IGNORECASE)) or
        soup.find(class_=re.compile(r"content|main|article", re.IGNORECASE)) or
        soup.find("article") or
        soup.body
    )
    if main is None:
        return ""

    for tag in main.find_all(_INLINE_TAGS):
        tag.unwrap()
    main.smooth()   # joins the strings left side by side by unwrap into one
    text = main.get_text(separator="\n", strip=True).replace("\xa0", " ")

    # Filter noisy lines
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if len(line) < 4:
            continue
        if _NOISE_LINES.match(line):
            continue
        lines.append(line)

    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def extract_title(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    if soup.title:
        return soup.title.get_text(strip=True)
    h1 = soup.find("h1")
    return h1.get_text(strip=True) if h1 else ""


def extract_metadata(html: str, url: str) -> dict:
    """
    Extract additional metadata from the HTML page and the URL.

    Return a dict with:
      - category: content type (servizio, notizia, pagina, faq, documento)
      - section: subdomain/section of the site
      - date: publication/update date (if available)
      - service_status: "attivo" | "non attivo" | None (only for /services/)
    """
    soup = BeautifulSoup(html, "lxml")
    parsed = urlparse(url)
    path = parsed.path.lower()

    # --- Category from the URL pattern ---
    if "/services/" in path or "/service/" in path:
        category = "servizio"
    elif "/news/" in path or "/news-category/" in path or "/notizie/" in path:
        category = "notizia"
    elif "/faq" in path:
        category = "faq"
    elif "/public_documents/" in path or "/documents/" in path:
        category = "documento"
    elif "/topics/" in path:
        category = "argomento"
    else:
        category = "pagina"

    # --- Section from the domain ---
    netloc = parsed.netloc.lower()
    section = next((r["section"] for r in CRAWL_SECTION_RULES
                    if r.get("host_contains") and r["host_contains"] in netloc), SECTION_DEFAULT)

    # --- Publication/update date ---
    date = None
    for meta_name in ["article:modified_time", "article:published_time",
                       "date", "DC.date", "last-modified"]:
        tag = soup.find("meta", property=meta_name) or soup.find("meta", attrs={"name": meta_name})
        if tag and tag.get("content"):
            date = tag["content"][:10]  # YYYY-MM-DD only
            break
    if not date:
        # Look in the <time> tag
        time_tag = soup.find("time", datetime=True)
        if time_tag:
            date = time_tag["datetime"][:10]

    # --- Service status (only for /services/ pages) ---
    service_status = None
    if category == "servizio":
        page_text = soup.get_text(" ", strip=True).lower()
        if "servizio attivo" in page_text:
            service_status = "attivo"
        elif "servizio non attivo" in page_text or "servizio sospeso" in page_text:
            service_status = "non attivo"

    result = {
        "category": category,
        "section": section,
    }
    if date:
        result["date"] = date
    if service_status:
        result["service_status"] = service_status

    return result


async def crawl(start_url: str = SITE_URL, incremental: bool = False) -> dict:
    """
    Crawl the site.

    incremental=True → skip pages whose content has not changed
                        since the last scan (hash comparison).
    Return a dict with 'pages' and 'pdfs', including unchanged results
    (needed to keep the index complete).
    """
    CRAWL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
    pages_dir = CRAWL_CACHE_DIR / "pages"
    pages_dir.mkdir(exist_ok=True)

    state = CrawlState()
    state.reset_changed_flags()

    visited: set[str] = set()
    to_visit: list[str] = [start_url] + CRAWL_EXTRA_START_URLS
    queued: set[str] = set(to_visit)                 # O(1) test instead of a scan of the list
    variants: dict[tuple[str, str], int] = {}        # (host, path) -> query-string variants queued
    pdf_urls: set[str] = set()
    pdf_link_texts: dict[str, list[str]] = {}  # PDF URL → texts of the links that cite it
    results_pages = []
    results_pdfs = []

    # In incremental mode we load the existing index as a base
    existing_index: dict = {"pages": [], "pdfs": []}
    index_path = CRAWL_CACHE_DIR / "index.json"
    if incremental and index_path.exists():
        existing_index = json.loads(index_path.read_text(encoding="utf-8"))
        # Pre-populate results with everything we already know
        results_pages = list(existing_index.get("pages", []))
        results_pdfs = list(existing_index.get("pdfs", []))
        log.info(
            f"Incremental mode — base: {len(results_pages)} pages, "
            f"{len(results_pdfs)} PDFs already indexed"
        )

    # PDFs already known but with a missing or invalid file (e.g. an HTML error page
    # saved in place of the PDF): dropped from the base so they get re-downloaded.
    if results_pdfs:
        valid_pdfs = [p for p in results_pdfs if _is_pdf_file(Path(p["file"]))]
        invalid = len(results_pdfs) - len(valid_pdfs)
        if invalid:
            log.warning(f"{invalid} known PDFs with a missing or invalid file: they will be downloaded again")
            results_pdfs = valid_pdfs

    known_page_urls = {p["url"] for p in results_pages}
    known_pdf_urls = {p["url"] for p in results_pdfs}

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; Zautte/1.0)"
        )
    }

    new_pages = 0
    updated_pages = 0
    skipped_pages = 0

    async with httpx.AsyncClient(
        headers=headers, follow_redirects=True, timeout=30.0, trust_env=False,
        event_hooks=guard_async(CRAWL_ALLOWED_DOMAINS),
    ) as client:

        while to_visit and (CRAWL_MAX_PAGES == 0 or len(visited) < CRAWL_MAX_PAGES):
            url = to_visit.pop(0)
            if url in visited:
                continue
            visited.add(url)

            try:
                limit_str = str(CRAWL_MAX_PAGES) if CRAWL_MAX_PAGES else "∞"
                log.info(f"[{len(visited)}/{limit_str}] {url}")
                resp = await _get(client, url)
                content_type = resp.headers.get("content-type", "")

                if resp.status_code >= 400:
                    # Error pages (404, 400, 503...) must not be indexed
                    # nor treated as content: they used to end up in the HTML parser.
                    log.warning(f"  HTTP error: {url} — {_describe(resp)}")
                    continue

                # --- HTML page ---
                if "text/html" in content_type:
                    raw_html = resp.text
                    page_hash = content_hash(raw_html)

                    # Always discover new links, even if the page has not changed
                    new_links = extract_links(raw_html, url)
                    for link in new_links:
                        if link in visited or link in queued:
                            continue
                        if not _variant_ok(link, variants):
                            continue
                        queued.add(link)
                        to_visit.append(link)
                    for pdf_url, link_text in extract_pdf_links(raw_html, url):
                        pdf_urls.add(pdf_url)
                        if link_text:
                            pdf_link_texts.setdefault(pdf_url, []).append(link_text)

                    # Skip if content unchanged (incremental mode only)
                    if incremental and not state.is_changed(url, page_hash):
                        log.debug("  Unchanged, skipping")
                        state.mark_unchanged(url)
                        skipped_pages += 1
                        continue

                    text = clean_text(raw_html)
                    title = extract_title(raw_html)
                    meta = extract_metadata(raw_html, url)

                    if len(text) < 100:
                        log.debug("  Text too short, skipping")
                        continue

                    fname = url_to_filename(url) + ".json"
                    fpath = pages_dir / fname
                    fpath.write_text(
                        json.dumps({"url": url, "title": title, "text": text, **meta},
                                   ensure_ascii=False),
                        encoding="utf-8"
                    )

                    page_entry = {"url": url, "title": title, "file": str(fpath), **meta}
                    state.update(url, page_hash, title, str(fpath))

                    if url in known_page_urls:
                        results_pages = [
                            page_entry if p["url"] == url else p
                            for p in results_pages
                        ]
                        updated_pages += 1
                        log.info(f"  UPDATED [{meta['category']}]: '{title}'")
                    else:
                        results_pages.append(page_entry)
                        known_page_urls.add(url)
                        new_pages += 1
                        log.info(f"  NEW [{meta['category']}]: '{title}' ({len(text)} chars)")

                # --- Direct PDF ---
                elif "application/pdf" in content_type:
                    if not _pdf_response_ok(resp):
                        log.warning(f"  Invalid PDF: {url} — {_describe(resp)}")
                        continue
                    pdf_hash = content_hash(resp.content)
                    if incremental and not state.is_changed(url, pdf_hash):
                        log.debug("  PDF unchanged, skipping")
                        state.mark_unchanged(url)
                        continue

                    fname = url_to_filename(url) + ".pdf"
                    fpath = DOCUMENTS_DIR / fname
                    fpath.write_bytes(resp.content)
                    state.update(url, pdf_hash, file=str(fpath))

                    pdf_entry = {"url": url, "file": str(fpath)}
                    if url in known_pdf_urls:
                        results_pdfs = [
                            pdf_entry if p["url"] == url else p
                            for p in results_pdfs
                        ]
                        log.info(f"  PDF UPDATED: {fname}")
                    else:
                        results_pdfs.append(pdf_entry)
                        known_pdf_urls.add(url)
                        log.info(f"  PDF NEW: {fname} ({len(resp.content)//1024} KB)")

            except httpx.RequestError as e:
                log.warning(f"  Network error: {url} — {e}")
            except Exception:
                log.exception(f"  Unexpected error: {url}")

            await asyncio.sleep(CRAWL_DELAY_SECONDS)

        # --- PDFs found in pages ---
        new_pdfs = pdf_urls - known_pdf_urls
        if new_pdfs:
            log.info(f"\nDownloading {len(new_pdfs)} new PDFs...")
        for pdf_url in new_pdfs:
            if pdf_url in visited:
                continue
            visited.add(pdf_url)
            try:
                resp = await _get(client, pdf_url)
                if not _pdf_response_ok(resp):
                    log.warning(f"  Invalid PDF: {pdf_url} — {_describe(resp)}")
                    continue
                # A PDF not in the index must always be saved and marked
                # changed, even if the state already knows its hash: otherwise
                # a PDF dropped from the index (corrupt file, removal) would stay
                # out forever. Reindexing it is cheap (per-chunk comparison).
                pdf_hash = content_hash(resp.content)
                fname = url_to_filename(pdf_url) + ".pdf"
                fpath = DOCUMENTS_DIR / fname
                fpath.write_bytes(resp.content)
                state.update(pdf_url, pdf_hash, file=str(fpath))
                results_pdfs.append({"url": pdf_url, "file": str(fpath)})
                log.info(f"  PDF: {fname} ({len(resp.content)//1024} KB)")
            except (httpx.HTTPError, OSError) as e:
                log.warning(f"  PDF error {pdf_url}: {e}")
            await asyncio.sleep(CRAWL_DELAY_SECONDS)

        # --- Remove pages that disappeared from the site ---
        # Already-known PDFs that are still linked are not re-downloaded in incremental mode,
        # but they are still on the site: without this line they were reported as removed and
        # re-downloaded as new the following week (109 «RIMOSSO» and 142 «nuovi» on 05/10/2026).
        visited |= pdf_urls & known_pdf_urls
        removed = state.get_removed_urls(visited)
        if removed:
            log.info(f"\n{len(removed)} URLs no longer found on the site:")
            for url in removed:
                log.info(f"  REMOVED: {url}")
                state.remove(url)
                results_pages = [p for p in results_pages if p["url"] != url]
                results_pdfs  = [p for p in results_pdfs  if p["url"] != url]

    # Save state and index
    state.save()
    updated = save_link_texts(pdf_link_texts)
    log.info(f"PDF link texts: {len(pdf_link_texts)} PDFs cited, {updated} new or changed titles")
    index = {"pages": results_pages, "pdfs": results_pdfs}
    write_json_atomic(index_path, index, indent=2)   # a run cut short never leaves a truncated index
    n_old = state.delete_orphans()
    if n_old:
        log.info(f"{n_old} cache files under the old name removed")

    mode = "incrementale" if incremental else "completo"
    log.info(f"=== Crawl {mode} completed ===")
    if incremental:
        log.info(f"New: {new_pages} | Updated: {updated_pages} | Unchanged: {skipped_pages}")
    log.info(f"Index total: {len(results_pages)} pages, {len(results_pdfs)} PDFs")

    return index


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Crawler for the organisation's website")
    parser.add_argument("--incremental", action="store_true",
                        help="Download only the pages modified since the last crawl")
    args = parser.parse_args()
    asyncio.run(crawl(incremental=args.incremental))
