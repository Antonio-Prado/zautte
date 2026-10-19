"""
Main synchronisation script.
Orchestrates crawl + indexing in a single command.

Modes:
  full         → full crawl + complete reindexing (first installation)
  incremental  → incremental crawl + index only the changes (periodic use)
  inbox        → process only the documents in the inbox folder
  full-index   → no crawl, reindex from scratch everything already downloaded
  news         → new items from the Bulletin and the feeds, with pages and attachments
                 (every 15 minutes from cron; --backfill = whole archive);
                 hot-reloads the index in the API instead of restarting it

Usage:
  python -m scripts.sync full
  python -m scripts.sync incremental
  python -m scripts.sync inbox
  python -m scripts.sync full-index
  python -m scripts.sync news [--backfill]
"""

import argparse
import asyncio
import logging
import logging.handlers
import os
import signal
import subprocess
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import ADMIN_API_KEY, API_PORT, is_migrated_source, now_local
from crawler.crawler import crawl
from indexer.indexer import index_pages, index_pdfs, load_index
from indexer.vector_store import (
    clear_collection,
    get_indexed_sources,
    get_stats,
    get_zero_vector_chunks,
    remove_sources,
    update_embeddings,
)
from scripts.inbox_indexer import process_inbox
from scripts.news_sync import run_news

LOG_FILE = Path("/var/log/chatbot-sync.log")

def _setup_logging():
    """
    Logging to file with WatchedFileHandler (survives newsyslog rotation)
    + stdout for interactive use.
    """
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # File: reopens automatically if the file is rotated (inode changes)
    try:
        fh = logging.handlers.WatchedFileHandler(str(LOG_FILE), encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError as e:
        print(f"[WARN] impossibile aprire log file {LOG_FILE}: {e}", file=sys.stderr)

    # Stdout: useful when launched manually
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)

_setup_logging()
log = logging.getLogger(__name__)


def print_separator(title: str = ""):
    line = "=" * 60
    if title:
        log.info(f"\n{line}")
        log.info(f"  {title}")
        log.info(line)
    else:
        log.info(line)


async def run_sync(mode: str, backfill: bool = False):
    started_at = now_local()
    print_separator(f"SYNC — modalità: {mode.upper()}")

    if mode == "full":
        print_separator("Fase 1/3: Crawl completo del sito")
        index = await crawl(incremental=False)

        print_separator("Fase 2/3: Reindicizzazione completa (senza svuotare l'indice)")
        index_pages(index["pages"], replace_existing=True)
        index_pdfs(index["pdfs"], replace_existing=True)

        # Remove sources that no longer exist on the site.
        # Migrated domains (MIGRATED_DOMAINS) are EXCLUDED: their resources are
        # no longer crawlable (the portal redirects to a generic landing page), but the
        # text already indexed is still valid — they must not be treated as "stale",
        # otherwise the full sync would delete a large part of the knowledge base.
        current_urls = {p["url"] for p in index["pages"]} | {p["url"] for p in index["pdfs"]}
        stale = {
            s for s in (get_indexed_sources() - current_urls)
            if not is_migrated_source(s)
        }
        if stale:
            log.info(f"Pulizia: {len(stale)} sorgenti non più presenti nel sito")
            remove_sources(stale)

        print_separator("Fase 3/3: Inbox documenti")
        process_inbox()

    elif mode == "incremental":
        print_separator("Fase 1/3: Crawl incrementale")
        index = await crawl(incremental=True)

        print_separator("Fase 2/3: Indicizzazione modifiche")
        from crawler.state import CrawlState
        state = CrawlState()
        changed_urls = set(state.get_changed_urls())

        changed_pages = [p for p in index["pages"] if p["url"] in changed_urls]
        changed_pdfs  = [p for p in index["pdfs"]  if p["url"] in changed_urls]

        if changed_pages or changed_pdfs:
            index_pages(changed_pages)
            index_pdfs(changed_pdfs)
        else:
            log.info("Nessuna modifica rilevata, niente da reindicizzare.")

        # Cleanup of HTML sources no longer present in the current crawl
        current_html_urls = {p["url"] for p in index["pages"]}
        all_indexed = get_indexed_sources()
        stale_html = {s for s in all_indexed if s not in current_html_urls
                      and not s.lower().endswith(".pdf")
                      and not is_migrated_source(s)}
        if stale_html:
            log.info(f"Pulizia: {len(stale_html)} sorgenti HTML stale da rimuovere")
            remove_sources(stale_html)

        print_separator("Fase 3/3: Inbox documenti")
        process_inbox()

    elif mode == "reembed":
        print_separator("Re-embedding chunk con vettore zero")
        from indexer.embedder import embed_texts
        candidates = get_zero_vector_chunks()
        if not candidates:
            log.info("Nessun chunk con vettore zero trovato — indice pulito.")
        else:
            log.info(f"Trovati {len(candidates)} chunk da ri-embedare")
            texts = [c["text"] for c in candidates]
            embeddings = embed_texts(texts)
            updates = [(c["idx"], emb) for c, emb in zip(candidates, embeddings)]
            updated = update_embeddings(updates)
            remaining = len(candidates) - updated
            log.info(f"Re-embedding completato: {updated}/{len(candidates)} aggiornati"
                     + (f", {remaining} ancora con vettore zero" if remaining else ""))

    elif mode == "inbox":
        print_separator("Fase 1/1: Inbox documenti")
        process_inbox()

    elif mode == "news":
        print_separator("Fase 1/1: Notizie" + (" (archivio completo)" if backfill else ""))
        news = run_news(backfill=backfill)
        if not any(news[k] for k in ("posts", "pages", "attachments", "chunks")):
            return   # nothing new: no summary, no reload

    elif mode == "full-index":
        print_separator("Fase 1/2: Reset e reindicizzazione da file esistenti")
        index = load_index()
        clear_collection()
        index_pages(index["pages"])
        index_pdfs(index["pdfs"])
        print_separator("Fase 2/2: Inbox documenti")
        process_inbox()

    else:
        log.error(f"Modalità sconosciuta: {mode}")
        sys.exit(1)

    elapsed = (now_local() - started_at).total_seconds()
    stats = get_stats()

    print_separator("RIEPILOGO")
    log.info(f"Modalità:              {mode}")
    log.info(f"Durata:                {elapsed:.0f}s")
    log.info(f"Chunk totali nel DB:   {stats['total_chunks']}")
    log.info(f"Completato alle:       {now_local().strftime('%Y-%m-%d %H:%M:%S')}")
    print_separator()
    if mode == "news":
        _reload_api()
        if backfill:
            _notify(f"Zautte: archivio notizie indicizzato ({news['posts']} articoli)",
                    f"Modalità: news --backfill\nArticoli: {news['posts']}\nPagine: {news['pages']}\n"
                    f"Allegati: {news['attachments']}\nChunk inseriti: {news['chunks']}\n"
                    f"Chunk totali: {stats['total_chunks']}\nDurata: {elapsed:.0f}s")
    else:
        _restart_api()


def _reload_api():
    """Ask the API to reload the store from disk (POST /admin/reload): no
    restart, no 502. If the API is down it does nothing: at the next start it will
    load the updated files anyway."""
    try:
        resp = httpx.post(f"http://127.0.0.1:{API_PORT}/admin/reload",
                          headers={"X-Admin-Key": ADMIN_API_KEY}, timeout=600.0)
        if resp.status_code == 200 and resp.json().get("reloaded"):
            log.info(f"Reload API: {resp.json().get('total_chunks')} chunk in memoria")
        else:
            log.warning(f"Reload API non riuscito: HTTP {resp.status_code} {resp.text[:200]}")
    except (httpx.HTTPError, ValueError) as e:
        log.warning(f"Reload API fallito: {e}")


def _notify(subject: str, body: str):
    """Report by email to FEEDBACK_NOTIFY_EMAIL for long runs launched by hand."""
    try:
        from api.mailer import send_email, smtp_configured
        from config.settings import FEEDBACK_NOTIFY_EMAIL
        if smtp_configured() and FEEDBACK_NOTIFY_EMAIL:
            send_email(FEEDBACK_NOTIFY_EMAIL, subject, body)
    except (OSError, ValueError, RuntimeError) as e:
        log.warning(f"Email di riepilogo non inviata: {e}")


def _restart_api():
    """Send SIGKILL to the API process: the watchdog will relaunch it and load the updated VS."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "api.main:app"],
            capture_output=True, text=True, check=False
        )
        pids = [int(p) for p in result.stdout.split() if p.strip()]
        if not pids:
            log.warning("Restart API: nessun processo uvicorn trovato")
            return
        for pid in pids:
            os.kill(pid, signal.SIGKILL)
        log.info(f"Restart API: SIGKILL inviato a {len(pids)} processi uvicorn "
                 f"(PID: {', '.join(map(str, pids))})")
    except (OSError, ValueError) as e:
        log.warning(f"Restart API fallito: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Sincronizzazione contenuti: crawl + indicizzazione"
    )
    parser.add_argument(
        "mode",
        choices=["full", "incremental", "inbox", "full-index", "reembed", "news"],
        help=(
            "full=crawl completo + reindicizza tutto | "
            "incremental=solo modifiche | "
            "inbox=solo nuovi documenti | "
            "full-index=reindicizza senza crawl | "
            "reembed=ri-embeda chunk con vettore zero | "
            "news=notizie nuove dal Bollettino e dai feed"
        )
    )
    parser.add_argument("--backfill", action="store_true",
                        help="con news: tutto l'archivio del Bollettino, non solo le novità")
    args = parser.parse_args()
    asyncio.run(run_sync(args.mode, backfill=args.backfill))


if __name__ == "__main__":
    main()
