"""
Indexer principale: legge l'output del crawler, estrae testo, genera
embedding e popola il vector store. Può essere rieseguito (upsert idempotente).

Per ogni documento si confrontano i chunk nuovi con quelli già nello store
(stesso id e stesso testo, vettore non nullo): solo i chunk cambiati o mai
embeddati passano da Ollama, gli altri aggiornano soltanto i metadati.
I chunk di una sorgente che non compaiono più nella sua versione aggiornata
vengono rimossi in blocco ai checkpoint. Le scritture su disco sono
differite (vedi vector_store.deferred_saves): un salvataggio ogni
CHECKPOINT_INTERVAL invece di due per documento.

Uso:
    python -m indexer.indexer              # indicizza tutto
    python -m indexer.indexer --reset      # svuota e reindicizza
    python -m indexer.indexer --stats      # mostra statistiche
"""

import argparse
import json
import logging
import sys
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import CRAWL_CACHE_DIR
from indexer.pdf_extractor import extract_text_from_pdf, get_pdf_metadata
from indexer.chunker import chunk_document
from indexer.embedder import embed_texts
from indexer.vector_store import (
    upsert_chunks, get_stats, clear_collection, get_indexed_sources,
    chunk_id as make_chunk_id, chunks_to_embed, source_chunk_ids,
    remove_chunk_ids, deferred_saves, checkpoint, checkpoint_due,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

BATCH_SIZE = 50  # chunk da embeddare per ogni chiamata a embed_texts


class _Batcher:
    """Accumula i chunk in attesa di upsert e gli id stale da rimuovere.
    L'embedding parte quando i chunk da embeddare raggiungono BATCH_SIZE;
    rimozioni e salvataggio su disco avvengono ai checkpoint."""

    def __init__(self):
        self.chunks: list[dict] = []
        self.flags: list[bool] = []      # True = il chunk va embeddato
        self.to_embed = 0
        self.stale_ids: set[str] = set()
        self.total_inserted = 0
        self.total_removed = 0

    def add(self, chunks: list[dict], flags: list[bool], stale_ids: set[str]):
        self.chunks.extend(chunks)
        self.flags.extend(flags)
        self.to_embed += sum(flags)
        self.stale_ids |= stale_ids
        if self.to_embed >= BATCH_SIZE:
            self.flush()

    def flush(self):
        """Genera gli embedding mancanti e fa l'upsert di tutti i chunk pendenti."""
        if not self.chunks:
            return
        texts = [c["text"] for c, f in zip(self.chunks, self.flags) if f]
        vecs = iter(embed_texts(texts) if texts else [])
        embeddings = [next(vecs) if f else None for f in self.flags]
        inserted = upsert_chunks(self.chunks, embeddings)
        if texts:
            log.info(f"  → {inserted} chunk inseriti nel vector store")
        self.total_inserted += inserted
        self.chunks, self.flags, self.to_embed = [], [], 0

    def checkpoint(self, force: bool = False):
        """Ai checkpoint: flush, rimozione in blocco degli id stale, scrittura su disco."""
        if not force and not checkpoint_due():
            return
        self.flush()
        if self.stale_ids:
            self.total_removed += remove_chunk_ids(self.stale_ids)
            self.stale_ids = set()
        if checkpoint(force=True):
            log.info("  Checkpoint: vector store salvato su disco")

    def finish(self):
        self.checkpoint(force=True)


def load_index() -> dict:
    """Carica l'indice prodotto dal crawler."""
    index_path = CRAWL_CACHE_DIR / "index.json"
    if not index_path.exists():
        log.error(f"Indice crawler non trovato: {index_path}")
        log.error("Esegui prima il crawler: python -m crawler.crawler")
        sys.exit(1)
    with open(index_path, encoding="utf-8") as f:
        return json.load(f)


def _queue_document(batcher: _Batcher, chunks: list[dict], source: str) -> tuple[int, int]:
    """Decide quali chunk del documento vanno embeddati e quali chunk già nello
    store per la stessa sorgente sono diventati stale. Ritorna (da_embeddare, stale)."""
    ids = [
        make_chunk_id(c["text"], c["metadata"].get("source", ""), c["metadata"].get("chunk_index", 0))
        for c in chunks
    ]
    flags = chunks_to_embed(chunks, ids)
    stale = source_chunk_ids(source) - set(ids)
    batcher.add(chunks, flags, stale)
    return sum(flags), len(stale)


def _index_documents(
    docs: list[dict],
    label: str,
    make_chunks: Callable[[dict], list[dict] | None],
    skip_existing: bool,
) -> tuple[int, int]:
    """Ciclo comune a pagine e PDF. Ritorna (documenti_ok, chunk_inseriti)."""
    already_indexed = get_indexed_sources() if skip_existing else set()
    skipped = 0
    unchanged = 0
    docs_ok = 0
    batcher = _Batcher()

    with deferred_saves():
        for i, doc in enumerate(docs, 1):
            if skip_existing and doc["url"] in already_indexed:
                skipped += 1
                continue
            log.info(f"[{label} {i}/{len(docs)}] {doc['url']}")

            chunks = make_chunks(doc)
            if not chunks:
                continue

            n_embed, n_stale = _queue_document(batcher, chunks, doc["url"])
            detail = f"{n_embed} da embeddare"
            if n_stale:
                detail += f", {n_stale} stale da rimuovere"
            log.info(f"  {len(chunks)} chunk ({detail})")
            if n_embed == 0 and n_stale == 0:
                unchanged += 1
            docs_ok += 1
            batcher.checkpoint()

        batcher.finish()

    if skipped:
        log.info(f"  {skipped} {label} già indicizzati, saltati")
    if unchanged:
        log.info(f"  {unchanged} {label} invariati nel VS, embedding saltato")
    if batcher.total_removed:
        log.info(f"  {batcher.total_removed} chunk stale rimossi")
    return docs_ok, batcher.total_inserted


def _page_chunks(page: dict) -> list[dict] | None:
    """Legge il testo salvato dal crawler e lo spezza in chunk."""
    fpath = Path(page["file"])
    if not fpath.exists():
        log.warning(f"  File non trovato: {fpath}")
        return None

    with open(fpath, encoding="utf-8") as f:
        data = json.load(f)

    text = data.get("text", "").strip()
    if not text:
        log.debug("  Testo vuoto, skip")
        return None

    extra = {}
    for key in ("category", "section", "date", "service_status"):
        if key in page:
            extra[key] = page[key]

    return chunk_document(
        text=text,
        source_url=page["url"],
        title=page.get("title", ""),
        doc_type="html",
        extra_metadata=extra if extra else None,
    )


def _pdf_chunks(pdf: dict) -> list[dict] | None:
    """Estrae il testo dal PDF e lo spezza in chunk."""
    fpath = Path(pdf["file"])
    if not fpath.exists():
        log.warning(f"  File non trovato: {fpath}")
        return None

    text = extract_text_from_pdf(fpath)
    if not text:
        log.debug("  Testo vuoto (PDF scansionato o protetto?), skip")
        return None

    meta = get_pdf_metadata(fpath)
    title = meta["title"] or fpath.stem

    chunks = chunk_document(
        text=text,
        source_url=pdf["url"],
        title=title,
        doc_type="pdf",
        extra_metadata={"pdf_pages": meta["pages"]},
    )
    if chunks:
        log.info(f"  {meta['pages']} pagine")
    return chunks


def index_pages(pages: list[dict], skip_existing: bool = False, replace_existing: bool = False) -> tuple[int, int]:
    """Indicizza le pagine HTML. Ritorna (documenti_ok, chunk_inseriti).

    `replace_existing` è mantenuto per compatibilità: il confronto per chunk
    (testo e vettore) vale in ogni modalità, quindi anche il sync full
    riembedda solo ciò che è cambiato e rimuove i chunk stale della sorgente.
    """
    return _index_documents(pages, "HTML", _page_chunks, skip_existing)


def index_pdfs(pdfs: list[dict], skip_existing: bool = False, replace_existing: bool = False) -> tuple[int, int]:
    """Indicizza i documenti PDF. Ritorna (documenti_ok, chunk_inseriti).
    Vedi index_pages per il significato di `replace_existing`."""
    return _index_documents(pdfs, "PDF", _pdf_chunks, skip_existing)


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
