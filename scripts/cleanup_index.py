"""
Cleanup of the existing index after the crawler filters were updated.

The script:
  1. Reads data/crawl_cache/index.json and data/crawl_cache/crawl_state.json
  2. Applies the same should_skip() logic as the updated crawler
  3. Removes entries whose URLs are now blocked
  4. Deletes the corresponding .json and .pdf files
  5. Saves the cleaned index.json and crawl_state.json

Usage:
  python scripts/cleanup_index.py [--dry-run]

With --dry-run it only shows how many entries would be removed, without changing anything.
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import (
    CRAWL_ALLOWED_DOMAINS,
    CRAWL_CACHE_DIR,
    CRAWL_DOMAIN_MAX_PATH_DEPTH,
    CRAWL_EXCLUDE_PATTERNS,
    CRAWL_MAX_PATH_DEPTH,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def should_skip(url: str) -> bool:
    """Exact replica of the updated crawler's should_skip() logic."""
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        return True

    if "@" in parsed.netloc:
        return True

    if not any(d in parsed.netloc for d in CRAWL_ALLOWED_DOMAINS):
        return True

    full = parsed.path + ("?" + parsed.query if parsed.query else "")
    if any(pat in full for pat in CRAWL_EXCLUDE_PATTERNS):
        return True

    segments = [s for s in parsed.path.split("/") if s]
    domain_key = next(
        (k for k in CRAWL_DOMAIN_MAX_PATH_DEPTH if k in parsed.netloc), None
    )
    max_depth = CRAWL_DOMAIN_MAX_PATH_DEPTH[domain_key] if domain_key else CRAWL_MAX_PATH_DEPTH
    if len(segments) > max_depth:
        return True

    non_numeric = [s for s in segments if not s.isdigit()]
    return len(non_numeric) != len(set(non_numeric))


def cleanup(dry_run: bool = False):
    index_path = CRAWL_CACHE_DIR / "index.json"
    state_path = CRAWL_CACHE_DIR / "crawl_state.json"

    if not index_path.exists():
        log.error(f"index.json non trovato in {CRAWL_CACHE_DIR}")
        sys.exit(1)

    with open(index_path, encoding="utf-8") as f:
        index = json.load(f)

    state: dict = {}
    if state_path.exists():
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)

    pages_in  = index.get("pages", [])
    pdfs_in   = index.get("pdfs",  [])
    log.info(f"Indice attuale: {len(pages_in)} pagine, {len(pdfs_in)} PDF")
    log.info(f"Stato attuale:  {len(state)} URL tracciati")

    pages_keep, pages_drop = [], []
    for entry in pages_in:
        (pages_drop if should_skip(entry["url"]) else pages_keep).append(entry)

    pdfs_keep, pdfs_drop = [], []
    for entry in pdfs_in:
        (pdfs_drop if should_skip(entry["url"]) else pdfs_keep).append(entry)

    # Count the URLs in the state that would be removed (including entries without a file)
    state_drop = {url for url in state if should_skip(url)}

    log.info(
        f"\nPagine:  {len(pages_keep)} rimangono, {len(pages_drop)} rimosse"
    )
    log.info(
        f"PDF:     {len(pdfs_keep)} rimangono, {len(pdfs_drop)} rimossi"
    )
    log.info(
        f"State:   {len(state) - len(state_drop)} rimangono, {len(state_drop)} rimossi"
    )

    if dry_run:
        log.info("\n[DRY-RUN] Nessuna modifica applicata.")
        # Show a sample of the removed URLs per category
        _show_sample("Pagine rimosse (campione)", [e["url"] for e in pages_drop])
        _show_sample("PDF rimossi (campione)", [e["url"] for e in pdfs_drop])
        return

    # --- Delete the files of the removed entries ---
    files_deleted = 0
    for entry in pages_drop + pdfs_drop:
        fpath = Path(entry.get("file", ""))
        if fpath.exists():
            fpath.unlink()
            files_deleted += 1

    log.info(f"\nFile eliminati dal disco: {files_deleted}")

    # --- Save the cleaned index.json ---
    cleaned_index = {"pages": pages_keep, "pdfs": pdfs_keep}
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(cleaned_index, f, ensure_ascii=False, indent=2)
    log.info(f"index.json aggiornato → {len(pages_keep)} pagine, {len(pdfs_keep)} PDF")

    # --- Save the cleaned crawl_state.json ---
    if state_path.exists():
        cleaned_state = {url: data for url, data in state.items() if url not in state_drop}
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(cleaned_state, f, ensure_ascii=False, indent=2)
        log.info(f"crawl_state.json aggiornato → {len(cleaned_state)} URL tracciati")

    log.info("\nPulizia completata.")
    log.info("Prossimo passo: re-indicizza con   python -m indexer.indexer")


def _show_sample(label: str, urls: list[str], n: int = 5):
    if not urls:
        return
    log.info(f"\n{label} ({len(urls)} totali):")
    for url in urls[:n]:
        log.info(f"  {url}")
    if len(urls) > n:
        log.info(f"  ... e altri {len(urls) - n}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pulizia indice crawler")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Mostra cosa verrebbe rimosso senza modificare nulla"
    )
    args = parser.parse_args()
    cleanup(dry_run=args.dry_run)
