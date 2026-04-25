"""
Crawler asincrono per siti web statici e CMS.
Scarica pagine HTML e documenti PDF, salvando i risultati in data/crawl_cache/.

Supporta due modalità:
  - Completo (default): riscansiona tutto il sito
  - Incrementale (--incremental): scarica solo pagine modificate dall'ultima scansione
"""

import argparse
import asyncio
import hashlib
import json
import logging
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse, urldefrag

import httpx
from bs4 import BeautifulSoup

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import (
    SITE_URL, CRAWL_MAX_PAGES, CRAWL_DELAY_SECONDS,
    CRAWL_ALLOWED_DOMAINS, CRAWL_EXCLUDE_PATTERNS,
    CRAWL_MAX_PATH_DEPTH, CRAWL_DOMAIN_MAX_PATH_DEPTH,
    CRAWL_CACHE_DIR, DOCUMENTS_DIR
)
from crawler.state import CrawlState, content_hash

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)


def url_to_filename(url: str) -> str:
    h = hashlib.md5(url.encode()).hexdigest()[:12]
    path = urlparse(url).path.strip("/").replace("/", "_")[:60]
    return f"{path}_{h}" if path else h


def should_skip(url: str) -> bool:
    parsed = urlparse(url)

    # Protocolli non-HTTP (webcal, ftp, ecc.)
    if parsed.scheme not in ("http", "https"):
        return True

    # Indirizzi email mascherati da URL (es. http://utente@dominio.it)
    if "@" in parsed.netloc:
        return True

    # Dominio non consentito
    if not any(d in parsed.netloc for d in CRAWL_ALLOWED_DOMAINS):
        return True

    # Pattern esclusi (estensioni, path vietati, script PHP, ecc.)
    full = parsed.path + ("?" + parsed.query if parsed.query else "")
    if any(pat in full for pat in CRAWL_EXCLUDE_PATTERNS):
        return True

    # Profondità massima del path: previene l'esplosione su siti con
    # navigazione cumulativa.
    # Il limite per-dominio (più restrittivo) ha priorità su quello globale.
    segments = [s for s in parsed.path.split("/") if s]
    domain_key = next(
        (k for k in CRAWL_DOMAIN_MAX_PATH_DEPTH if k in parsed.netloc), None
    )
    max_depth = CRAWL_DOMAIN_MAX_PATH_DEPTH[domain_key] if domain_key else CRAWL_MAX_PATH_DEPTH
    if len(segments) > max_depth:
        return True

    # Loop detector: se un segmento non numerico appare più volte nel path,
    # è probabile che il CMS stia accumulando breadcrumb → scarta l'URL.
    non_numeric = [s for s in segments if not s.isdigit()]
    if len(non_numeric) != len(set(non_numeric)):
        return True

    return False


def extract_links(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    links = []
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if href.startswith(("mailto:", "tel:", "webcal:", "javascript:", "#")):
            continue
        absolute = urljoin(base_url, href)
        absolute, _ = urldefrag(absolute)
        if not should_skip(absolute):
            links.append(absolute)
    return links


def extract_pdf_links(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    pdfs = []
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if href.lower().endswith(".pdf"):
            absolute = urljoin(base_url, href)
            if any(d in urlparse(absolute).netloc for d in CRAWL_ALLOWED_DOMAINS):
                pdfs.append(absolute)
    return pdfs


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


def clean_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")

    # Rimuovi tag non utili
    for tag in soup.find_all(["script", "style", "nav", "header", "footer",
                               "aside", "noscript", "iframe", "form"]):
        tag.decompose()

    # Rimuovi widget di feedback/valutazione
    for tag in soup.find_all(class_=re.compile(
        r"feedback|rating|survey|cookie|breadcrumb|pagination|share|social|"
        r"widget|banner|alert|modal|tooltip|dropdown|collapse", re.I
    )):
        tag.decompose()

    main = (
        soup.find("main") or
        soup.find(id=re.compile(r"content|main|body", re.I)) or
        soup.find(class_=re.compile(r"content|main|article", re.I)) or
        soup.find("article") or
        soup.body
    )
    if main is None:
        return ""

    text = main.get_text(separator="\n", strip=True)

    # Filtra righe rumorose
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
    Estrae metadati aggiuntivi dalla pagina HTML e dall'URL.

    Ritorna dict con:
      - category: tipo di contenuto (servizio, notizia, pagina, faq, documento)
      - section: sottodominio/sezione del sito
      - date: data pubblicazione/aggiornamento (se disponibile)
      - service_status: "attivo" | "non attivo" | None (solo per /services/)
    """
    soup = BeautifulSoup(html, "lxml")
    parsed = urlparse(url)
    path = parsed.path.lower()

    # --- Categoria dal pattern URL ---
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

    # --- Sezione dal dominio ---
    netloc = parsed.netloc.lower()
    if "amministrazionetrasparente" in netloc:
        section = "trasparenza"
    elif "sportellounico" in netloc or "suap" in netloc:
        section = "suap"
    elif "ambitosociale" in netloc:
        section = "ambito_sociale"
    elif "servizi." in netloc:
        section = "servizi_online"
    else:
        section = "sito_principale"

    # --- Data di pubblicazione/aggiornamento ---
    date = None
    for meta_name in ["article:modified_time", "article:published_time",
                       "date", "DC.date", "last-modified"]:
        tag = soup.find("meta", property=meta_name) or soup.find("meta", attrs={"name": meta_name})
        if tag and tag.get("content"):
            date = tag["content"][:10]  # solo YYYY-MM-DD
            break
    if not date:
        # Cerca nel tag <time>
        time_tag = soup.find("time", datetime=True)
        if time_tag:
            date = time_tag["datetime"][:10]

    # --- Stato del servizio (solo per pagine /services/) ---
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
    Crawl del sito.

    incremental=True → salta le pagine il cui contenuto non è cambiato
                        dall'ultima scansione (confronto hash).
    Ritorna dict con 'pages' e 'pdfs', inclusi i risultati invariati
    (necessari per mantenere l'indice completo).
    """
    CRAWL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
    pages_dir = CRAWL_CACHE_DIR / "pages"
    pages_dir.mkdir(exist_ok=True)

    state = CrawlState()
    state.reset_changed_flags()

    visited: set[str] = set()
    to_visit: list[str] = [start_url]
    pdf_urls: set[str] = set()
    results_pages = []
    results_pdfs = []

    # In modalità incrementale carichiamo l'indice esistente come base
    existing_index: dict = {"pages": [], "pdfs": []}
    index_path = CRAWL_CACHE_DIR / "index.json"
    if incremental and index_path.exists():
        with open(index_path, encoding="utf-8") as f:
            existing_index = json.load(f)
        # Pre-popola results con tutto ciò che già conosciamo
        results_pages = list(existing_index.get("pages", []))
        results_pdfs = list(existing_index.get("pdfs", []))
        log.info(
            f"Modalità incrementale — base: {len(results_pages)} pagine, "
            f"{len(results_pdfs)} PDF già indicizzati"
        )

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
        headers=headers, follow_redirects=True, timeout=30.0
    ) as client:

        while to_visit and (CRAWL_MAX_PAGES == 0 or len(visited) < CRAWL_MAX_PAGES):
            url = to_visit.pop(0)
            if url in visited:
                continue
            visited.add(url)

            try:
                limit_str = str(CRAWL_MAX_PAGES) if CRAWL_MAX_PAGES else "∞"
                log.info(f"[{len(visited)}/{limit_str}] {url}")
                resp = await client.get(url)
                content_type = resp.headers.get("content-type", "")

                # --- Pagina HTML ---
                if "text/html" in content_type:
                    raw_html = resp.text
                    page_hash = content_hash(raw_html)

                    # Scopri nuovi link sempre, anche se la pagina non è cambiata
                    new_links = extract_links(raw_html, url)
                    for link in new_links:
                        if link not in visited and link not in to_visit:
                            to_visit.append(link)
                    for pdf_url in extract_pdf_links(raw_html, url):
                        pdf_urls.add(pdf_url)

                    # Salta se contenuto invariato (solo in modalità incrementale)
                    if incremental and not state.is_changed(url, page_hash):
                        log.debug(f"  Invariata, skip")
                        state.mark_unchanged(url)
                        skipped_pages += 1
                        continue

                    text = clean_text(raw_html)
                    title = extract_title(raw_html)
                    meta = extract_metadata(raw_html, url)

                    if len(text) < 100:
                        log.debug(f"  Testo troppo corto, skip")
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
                        log.info(f"  AGGIORNATA [{meta['category']}]: '{title}'")
                    else:
                        results_pages.append(page_entry)
                        known_page_urls.add(url)
                        new_pages += 1
                        log.info(f"  NUOVA [{meta['category']}]: '{title}' ({len(text)} chars)")

                # --- PDF diretto ---
                elif "application/pdf" in content_type:
                    pdf_hash = content_hash(resp.content)
                    if incremental and not state.is_changed(url, pdf_hash):
                        log.debug(f"  PDF invariato, skip")
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
                        log.info(f"  PDF AGGIORNATO: {fname}")
                    else:
                        results_pdfs.append(pdf_entry)
                        known_pdf_urls.add(url)
                        log.info(f"  PDF NUOVO: {fname} ({len(resp.content)//1024} KB)")

            except httpx.RequestError as e:
                log.warning(f"  Errore di rete: {url} — {e}")
            except Exception as e:
                log.error(f"  Errore: {url} — {e}")

            await asyncio.sleep(CRAWL_DELAY_SECONDS)

        # --- PDF trovati nelle pagine ---
        new_pdfs = pdf_urls - known_pdf_urls
        if new_pdfs:
            log.info(f"\nScaricamento {len(new_pdfs)} nuovi PDF...")
        for pdf_url in new_pdfs:
            if pdf_url in visited:
                continue
            visited.add(pdf_url)
            try:
                resp = await client.get(pdf_url)
                pdf_hash = content_hash(resp.content)
                if incremental and not state.is_changed(pdf_url, pdf_hash):
                    state.mark_unchanged(pdf_url)
                    continue
                fname = url_to_filename(pdf_url) + ".pdf"
                fpath = DOCUMENTS_DIR / fname
                fpath.write_bytes(resp.content)
                state.update(pdf_url, pdf_hash, file=str(fpath))
                results_pdfs.append({"url": pdf_url, "file": str(fpath)})
                log.info(f"  PDF: {fname} ({len(resp.content)//1024} KB)")
            except Exception as e:
                log.warning(f"  Errore PDF {pdf_url}: {e}")
            await asyncio.sleep(CRAWL_DELAY_SECONDS)

        # --- Rimuovi pagine scomparse dal sito ---
        removed = state.get_removed_urls(visited)
        if removed:
            log.info(f"\n{len(removed)} URL non più trovati nel sito:")
            for url in removed:
                log.info(f"  RIMOSSO: {url}")
                state.remove(url)
                results_pages = [p for p in results_pages if p["url"] != url]
                results_pdfs  = [p for p in results_pdfs  if p["url"] != url]

    # Salva stato e indice
    state.save()
    index = {"pages": results_pages, "pdfs": results_pdfs}
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

    mode = "incrementale" if incremental else "completo"
    log.info(f"\n=== Crawl {mode} completato ===")
    if incremental:
        log.info(f"Nuove: {new_pages} | Aggiornate: {updated_pages} | Invariate: {skipped_pages}")
    log.info(f"Totale indice: {len(results_pages)} pagine, {len(results_pdfs)} PDF")

    return index


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Crawler sito Comune SBT")
    parser.add_argument("--incremental", action="store_true",
                        help="Scarica solo le pagine modificate dall'ultima scansione")
    args = parser.parse_args()
    asyncio.run(crawl(incremental=args.incremental))
