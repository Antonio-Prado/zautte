"""
Script di sincronizzazione principale.
Orchestrata crawl + indicizzazione in un unico comando.

Modalità:
  full         → crawl completo + reindicizzazione totale (prima installazione)
  incremental  → crawl incrementale + indicizza solo le modifiche (uso periodico)
  inbox        → processa solo i documenti nella cartella inbox
  full-index   → non fa crawl, reindicizza da zero tutto quello già scaricato

Uso:
  python -m scripts.sync full
  python -m scripts.sync incremental
  python -m scripts.sync inbox
  python -m scripts.sync full-index
"""

import argparse
import asyncio
import logging
import logging.handlers
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from crawler.crawler import crawl
from indexer.indexer import index_pages, index_pdfs, load_index
from indexer.vector_store import get_stats, clear_collection, remove_sources, get_indexed_sources
from scripts.inbox_indexer import process_inbox

LOG_FILE = Path("/var/log/chatbot-sync.log")

def _setup_logging():
    """
    Logging su file con WatchedFileHandler (sopravvive alla rotazione newsyslog)
    + stdout per uso interattivo.
    """
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # File: riapre automaticamente se il file viene ruotato (inode cambia)
    try:
        fh = logging.handlers.WatchedFileHandler(str(LOG_FILE), encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except Exception as e:
        print(f"[WARN] impossibile aprire log file {LOG_FILE}: {e}", file=sys.stderr)

    # Stdout: utile quando lanciato manualmente
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


async def run_sync(mode: str):
    started_at = datetime.now()
    print_separator(f"SYNC — modalità: {mode.upper()}")

    if mode == "full":
        print_separator("Fase 1/3: Crawl completo del sito")
        index = await crawl(incremental=False)

        print_separator("Fase 2/3: Reset e reindicizzazione")
        clear_collection()
        index_pages(index["pages"])
        index_pdfs(index["pdfs"])

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

        # Pulizia sorgenti HTML non più presenti nel crawl corrente
        current_html_urls = {p["url"] for p in index["pages"]}
        all_indexed = get_indexed_sources()
        stale_html = {s for s in all_indexed if s not in current_html_urls
                      and not s.lower().endswith(".pdf")}
        if stale_html:
            log.info(f"Pulizia: {len(stale_html)} sorgenti HTML stale da rimuovere")
            remove_sources(stale_html)

        print_separator("Fase 3/3: Inbox documenti")
        process_inbox()

    elif mode == "inbox":
        print_separator("Fase 1/1: Inbox documenti")
        process_inbox()

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

    elapsed = (datetime.now() - started_at).total_seconds()
    stats = get_stats()

    print_separator("RIEPILOGO")
    log.info(f"Modalità:              {mode}")
    log.info(f"Durata:                {elapsed:.0f}s")
    log.info(f"Chunk totali nel DB:   {stats['total_chunks']}")
    log.info(f"Completato alle:       {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print_separator()


def main():
    parser = argparse.ArgumentParser(
        description="Sincronizzazione contenuti: crawl + indicizzazione"
    )
    parser.add_argument(
        "mode",
        choices=["full", "incremental", "inbox", "full-index"],
        help=(
            "full=crawl completo + reindicizza tutto | "
            "incremental=solo modifiche | "
            "inbox=solo nuovi documenti | "
            "full-index=reindicizza senza crawl"
        )
    )
    args = parser.parse_args()
    asyncio.run(run_sync(args.mode))


if __name__ == "__main__":
    main()
