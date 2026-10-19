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
        log.error(f"index.json not found in {CRAWL_CACHE_DIR}")
        sys.exit(1)

    with open(index_path, encoding="utf-8") as f:
        index = json.load(f)

    state: dict = {}
    if state_path.exists():
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)

    pages_in  = index.get("pages", [])
    pdfs_in   = index.get("pdfs",  [])
    log.info(f"Current index: {len(pages_in)} pages, {len(pdfs_in)} PDFs")
    log.info(f"Current state:  {len(state)} tracked URLs")

    pages_keep, pages_drop = [], []
    for entry in pages_in:
        (pages_drop if should_skip(entry["url"]) else pages_keep).append(entry)

    pdfs_keep, pdfs_drop = [], []
    for entry in pdfs_in:
        (pdfs_drop if should_skip(entry["url"]) else pdfs_keep).append(entry)

    # Count the URLs in the state that would be removed (including entries without a file)
    state_drop = {url for url in state if should_skip(url)}

    log.info(
        f"\nPages:   {len(pages_keep)} kept, {len(pages_drop)} removed"
    )
    log.info(
        f"PDFs:    {len(pdfs_keep)} kept, {len(pdfs_drop)} removed"
    )
    log.info(
        f"State:   {len(state) - len(state_drop)} kept, {len(state_drop)} removed"
    )

    if dry_run:
        log.info("\n[DRY-RUN] No changes applied.")
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

    log.info(f"\nFiles deleted from disk: {files_deleted}")

    # --- Save the cleaned index.json ---
    cleaned_index = {"pages": pages_keep, "pdfs": pdfs_keep}
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(cleaned_index, f, ensure_ascii=False, indent=2)
    log.info(f"index.json updated → {len(pages_keep)} pages, {len(pdfs_keep)} PDFs")

    # --- Save the cleaned crawl_state.json ---
    if state_path.exists():
        cleaned_state = {url: data for url, data in state.items() if url not in state_drop}
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(cleaned_state, f, ensure_ascii=False, indent=2)
        log.info(f"crawl_state.json updated → {len(cleaned_state)} tracked URLs")

    log.info("\nCleanup complete.")
    log.info("Next step: re-index with   python -m indexer.indexer")


def _show_sample(label: str, urls: list[str], n: int = 5):
    if not urls:
        return
    log.info(f"\n{label} ({len(urls)} total):")
    for url in urls[:n]:
        log.info(f"  {url}")
    if len(urls) > n:
        log.info(f"  ... and {len(urls) - n} more")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Crawler index cleanup")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be removed without changing anything"
    )
    args = parser.parse_args()
    cleanup(dry_run=args.dry_run)
