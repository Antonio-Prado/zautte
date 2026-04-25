"""
Indexer principale: legge l'output del crawler, estrae testo, genera
embedding e popola ChromaDB. Può essere rieseguito (upsert idempotente).

Uso:
    python -m indexer.indexer              # indicizza tutto
    python -m indexer.indexer --reset      # svuota e reindicizza
    python -m indexer.indexer --stats      # mostra statistiche
"""

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import CRAWL_CACHE_DIR
from indexer.pdf_extractor import extract_text_from_pdf, get_pdf_metadata
from indexer.chunker import chunk_document
from indexer.embedder import embed_texts
from indexer.vector_store import upsert_chunks, get_stats, clear_collection, get_indexed_sources

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

BATCH_SIZE = 50  # chunk per batch di embedding


def load_index() -> dict:
    """Carica l'indice prodotto dal crawler."""
    index_path = CRAWL_CACHE_DIR / "index.json"
    if not index_path.exists():
        log.error(f"Indice crawler non trovato: {index_path}")
        log.error("Esegui prima il crawler: python -m crawler.crawler")
        sys.exit(1)
    with open(index_path, encoding="utf-8") as f:
        return json.load(f)


def index_pages(pages: list[dict], skip_existing: bool = False) -> tuple[int, int]:
    """Indicizza le pagine HTML. Ritorna (documenti_ok, chunk_totali)."""
    already_indexed = get_indexed_sources() if skip_existing else set()
    skipped = 0
    docs_ok = 0
    total_chunks = 0
    pending_chunks = []
    pending_embeddings_texts = []

    for i, page in enumerate(pages, 1):
        if skip_existing and page["url"] in already_indexed:
            skipped += 1
            continue
        log.info(f"[HTML {i}/{len(pages)}] {page['url']}")

        # Leggi il testo salvato dal crawler
        fpath = Path(page["file"])
        if not fpath.exists():
            log.warning(f"  File non trovato: {fpath}")
            continue

        with open(fpath, encoding="utf-8") as f:
            data = json.load(f)

        text = data.get("text", "").strip()
        if not text:
            log.debug("  Testo vuoto, skip")
            continue

        extra = {}
        for key in ("category", "section", "date", "service_status"):
            if key in page:
                extra[key] = page[key]

        chunks = chunk_document(
            text=text,
            source_url=page["url"],
            title=page.get("title", ""),
            doc_type="html",
            extra_metadata=extra if extra else None,
        )
        if not chunks:
            continue

        log.info(f"  {len(chunks)} chunk")
        pending_chunks.extend(chunks)
        pending_embeddings_texts.extend(c["text"] for c in chunks)
        docs_ok += 1

        # Processa in batch
        if len(pending_chunks) >= BATCH_SIZE:
            total_chunks += _flush(pending_chunks, pending_embeddings_texts)
            pending_chunks.clear()
            pending_embeddings_texts.clear()

    # Flush finale
    if pending_chunks:
        total_chunks += _flush(pending_chunks, pending_embeddings_texts)

    if skipped:
        log.info(f"  {skipped} pagine HTML già indicizzate, saltate")
    return docs_ok, total_chunks


def index_pdfs(pdfs: list[dict], skip_existing: bool = False) -> tuple[int, int]:
    """Indicizza i documenti PDF. Ritorna (documenti_ok, chunk_totali)."""
    already_indexed = get_indexed_sources() if skip_existing else set()
    skipped = 0
    docs_ok = 0
    total_chunks = 0
    pending_chunks = []
    pending_embeddings_texts = []

    for i, pdf in enumerate(pdfs, 1):
        if skip_existing and pdf["url"] in already_indexed:
            skipped += 1
            continue
        log.info(f"[PDF {i}/{len(pdfs)}] {pdf['url']}")

        fpath = Path(pdf["file"])
        if not fpath.exists():
            log.warning(f"  File non trovato: {fpath}")
            continue

        text = extract_text_from_pdf(fpath)
        if not text:
            log.debug("  Testo vuoto (PDF scansionato o protetto?), skip")
            continue

        meta = get_pdf_metadata(fpath)
        title = meta["title"] or fpath.stem

        chunks = chunk_document(
            text=text,
            source_url=pdf["url"],
            title=title,
            doc_type="pdf",
            extra_metadata={"pdf_pages": meta["pages"]},
        )
        if not chunks:
            continue

        log.info(f"  {len(chunks)} chunk da {meta['pages']} pagine")
        pending_chunks.extend(chunks)
        pending_embeddings_texts.extend(c["text"] for c in chunks)
        docs_ok += 1

        if len(pending_chunks) >= BATCH_SIZE:
            total_chunks += _flush(pending_chunks, pending_embeddings_texts)
            pending_chunks.clear()
            pending_embeddings_texts.clear()

    if pending_chunks:
        total_chunks += _flush(pending_chunks, pending_embeddings_texts)

    if skipped:
        log.info(f"  {skipped} PDF già indicizzati, saltati")
    return docs_ok, total_chunks


def _flush(chunks: list[dict], texts: list[str]) -> int:
    """Genera embedding e inserisce nel vector store. Ritorna chunk inseriti."""
    embeddings = embed_texts(texts)
    inserted = upsert_chunks(chunks, embeddings)
    log.info(f"  → {inserted} chunk inseriti nel vector store")
    return inserted


def main():
    parser = argparse.ArgumentParser(description="Indicizzatore contenuti Comune SBT")
    parser.add_argument("--reset", action="store_true",
                        help="Svuota il vector store prima di indicizzare")
    parser.add_argument("--stats", action="store_true",
                        help="Mostra statistiche e termina")
    parser.add_argument("--only-html", action="store_true",
                        help="Indicizza solo le pagine HTML")
    parser.add_argument("--only-pdf", action="store_true",
                        help="Indicizza solo i documenti PDF")
    args = parser.parse_args()

    if args.stats:
        stats = get_stats()
        print(f"\nVector store: {stats['collection']}")
        print(f"Chunk totali: {stats['total_chunks']}")
        return

    if args.reset:
        log.info("Reset del vector store...")
        clear_collection()

    index = load_index()
    pages = index.get("pages", [])
    pdfs = index.get("pdfs", [])

    log.info(f"Trovati: {len(pages)} pagine HTML, {len(pdfs)} documenti PDF")
    log.info("Caricamento modello embedding (prima esecuzione: download automatico)...")

    total_docs = 0
    total_chunks = 0

    skip = not args.reset  # salta i già indicizzati a meno di --reset

    if not args.only_pdf:
        docs, chunks = index_pages(pages, skip_existing=skip)
        total_docs += docs
        total_chunks += chunks
        log.info(f"HTML: {docs}/{len(pages)} documenti, {chunks} chunk")

    if not args.only_html:
        docs, chunks = index_pdfs(pdfs, skip_existing=skip)
        total_docs += docs
        total_chunks += chunks
        log.info(f"PDF: {docs}/{len(pdfs)} documenti, {chunks} chunk")

    stats = get_stats()
    log.info("\n=== Indicizzazione completata ===")
    log.info(f"Documenti processati: {total_docs}")
    log.info(f"Chunk inseriti questa sessione: {total_chunks}")
    log.info(f"Chunk totali nel vector store: {stats['total_chunks']}")


if __name__ == "__main__":
    main()
