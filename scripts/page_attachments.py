"""
Builds data/crawl_cache/page_attachments.json (page → PDFs it links) without a full crawl.

The crawler writes that file at every run; this script fills it at once for the pages already in
the crawl index (data/crawl_cache/index.json), reading each page again and collecting its PDF links
with the crawler's own rules. Nothing is downloaded but the pages, nothing is indexed. The search
reads the file after a reload of the API (POST /admin/reload) or a restart.

Run as root or as the sync account, under the lock of the syncs:
    LOCK=/var/db/zautte-sync/sync.lock; [ -d /var/db/zautte-sync ] || LOCK=/var/run/chatbot-sync.lock
    lockf -t 0 $LOCK venv/bin/python -m scripts.page_attachments [--limit N]
"""
import argparse
import json
import logging
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.privdrop import drop_to_sync_user

drop_to_sync_user()   # as root: become the sync account before config.settings reads .env

from config.settings import CRAWL_ALLOWED_DOMAINS, CRAWL_CACHE_DIR, CRAWL_DELAY_SECONDS
from crawler.crawler import extract_pdf_links, save_page_attachments
from crawler.netpolicy import get_limited

log = logging.getLogger(__name__)


def main() -> int:
    ap = argparse.ArgumentParser(description="Page → PDFs it links, for the pages already crawled")
    ap.add_argument("--limit", type=int, default=0, help="read only the first N pages (a test)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    index = json.loads((CRAWL_CACHE_DIR / "index.json").read_text(encoding="utf-8"))
    pages = [p["url"] for p in index.get("pages", []) if p.get("url")]
    if args.limit:
        pages = pages[:args.limit]
    observed: dict[str, list[str]] = {}
    failed = 0
    headers = {"User-Agent": "Mozilla/5.0 (compatible; Zautte/1.0)"}
    with httpx.Client(headers=headers, follow_redirects=False, timeout=30.0, trust_env=False) as client:
        for i, url in enumerate(pages, 1):
            try:
                resp = get_limited(client, url, CRAWL_ALLOWED_DOMAINS)
                if resp.status_code < 400 and "text/html" in resp.headers.get("content-type", ""):
                    observed[url] = [pdf for pdf, _ in extract_pdf_links(resp.text, url)]
                else:
                    failed += 1
            except (httpx.HTTPError, ValueError) as e:
                log.debug(f"{url}: {e}")
                failed += 1
            if i % 200 == 0:
                log.info(f"{i}/{len(pages)} pages read")
            time.sleep(CRAWL_DELAY_SECONDS)
    total = save_page_attachments(observed)
    with_pdfs = sum(1 for v in observed.values() if v)
    log.info(f"{len(observed)} pages read ({failed} not read), {with_pdfs} with PDFs; "
             f"{total} pages with attachments in the file")
    return 0


if __name__ == "__main__":
    sys.exit(main())
