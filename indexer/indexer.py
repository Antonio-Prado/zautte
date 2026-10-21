"""
Main indexer: reads the crawler output, extracts text, generates
embeddings and populates the vector store. Can be re-run (idempotent upsert).

For each document the new chunks are compared with those already in the store
(same id and same text, non-null vector): only changed or never-embedded
chunks go through Ollama, the others only update the metadata.
Chunks of a source that no longer appear in its updated version
are removed in bulk at checkpoints. Disk writes are
deferred (see vector_store.deferred_saves): one save every
CHECKPOINT_INTERVAL instead of two per document.

Usage:
    python -m indexer.indexer              # index everything
    python -m indexer.indexer --reset      # clear and reindex
    python -m indexer.indexer --stats      # show statistics
"""

import argparse
import json
import logging
import sys
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from api.privdrop import drop_to_sync_user

drop_to_sync_user()   # as root: become the sync account before config.settings reads .env

from config.settings import CRAWL_CACHE_DIR, DOCUMENTS_DIR
from indexer.chunker import chunk_document
from indexer.embedder import embed_texts
from indexer.pdf_extractor import extract_text_from_pdf, get_pdf_metadata
from indexer.vector_store import (
    checkpoint,
    checkpoint_due,
    chunks_to_embed,
    clear_collection,
    deferred_saves,
    get_indexed_sources,
    get_stats,
    remove_chunk_ids,
    source_chunk_ids,
    upsert_chunks,
)
from indexer.vector_store import (
    chunk_id as make_chunk_id,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

BATCH_SIZE = 50  # chunks to embed per embed_texts call


class _Batcher:
    """Accumulate chunks awaiting upsert and the stale ids to remove.
    Embedding starts when the chunks to embed reach BATCH_SIZE;
    removals and disk saves happen at checkpoints."""

    def __init__(self):
        self.chunks: list[dict] = []
        self.flags: list[bool] = []      # True = the chunk must be embedded
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
        """Generate the missing embeddings and upsert all pending chunks."""
        if not self.chunks:
            return
        texts = [c["text"] for c, f in zip(self.chunks, self.flags) if f]
        vecs = iter(embed_texts(texts) if texts else [])
        embeddings = [next(vecs) if f else None for f in self.flags]
        inserted = upsert_chunks(self.chunks, embeddings)
        if texts:
            log.info(f"  → {inserted} chunks inserted into the vector store")
        self.total_inserted += inserted
        self.chunks, self.flags, self.to_embed = [], [], 0

    def checkpoint(self, force: bool = False):
        """At checkpoints: flush, bulk removal of stale ids, write to disk."""
        if not force and not checkpoint_due():
            return
        self.flush()
        if self.stale_ids:
            self.total_removed += remove_chunk_ids(self.stale_ids)
            self.stale_ids = set()
        if checkpoint(force=True):
            log.info("  Checkpoint: vector store saved to disk")

    def finish(self):
        self.checkpoint(force=True)


def load_index() -> dict:
    """Load the index produced by the crawler."""
    index_path = CRAWL_CACHE_DIR / "index.json"
    if not index_path.exists():
        log.error(f"Crawler index not found: {index_path}")
        log.error("Run the crawler first: python -m crawler.crawler")
        sys.exit(1)
    with open(index_path, encoding="utf-8") as f:
        return json.load(f)


def _queue_document(batcher: _Batcher, chunks: list[dict], source: str) -> tuple[int, int]:
    """Decide which chunks of the document must be embedded and which chunks already in the
    store for the same source have become stale. Return (to_embed, stale)."""
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
    """Loop shared by pages and PDFs. Return (docs_ok, chunks_inserted)."""
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
            detail = f"{n_embed} to embed"
            if n_stale:
                detail += f", {n_stale} stale to remove"
            log.info(f"  {len(chunks)} chunks ({detail})")
            if n_embed == 0 and n_stale == 0:
                unchanged += 1
            docs_ok += 1
            batcher.checkpoint()

        batcher.finish()

    if skipped:
        log.info(f"  {skipped} {label} already indexed, skipped")
    if unchanged:
        log.info(f"  {unchanged} {label} unchanged in the VS, embedding skipped")
    if batcher.total_removed:
        log.info(f"  {batcher.total_removed} stale chunks removed")
    return docs_ok, batcher.total_inserted


def _inside(path: Path, base: Path) -> Path | None:
    """`path` if it really lies inside `base` (links resolved), else None. The index.json
    that names the files is written by the sync account: a path in it is not trusted."""
    try:
        resolved = path.resolve()
    except OSError:
        return None
    if resolved.is_relative_to(base.resolve()):
        return path
    log.warning(f"  Path outside {base.name}/ ignored: {path.name}")
    return None


def _page_chunks(page: dict) -> list[dict] | None:
    """Read the text saved by the crawler and split it into chunks."""
    fpath = _inside(Path(page["file"]), CRAWL_CACHE_DIR)
    if fpath is None or not fpath.exists():
        log.warning(f"  File not found: {fpath}")
        return None

    with open(fpath, encoding="utf-8") as f:
        data = json.load(f)

    text = data.get("text", "").strip()
    if not text:
        log.debug("  Empty text, skipping")
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
    """Extract the text from the PDF and split it into chunks."""
    fpath = _inside(Path(pdf["file"]), DOCUMENTS_DIR)
    if fpath is None or not fpath.exists():
        log.warning(f"  File not found: {fpath}")
        return None

    text = extract_text_from_pdf(fpath)
    if not text:
        log.debug("  Empty text (scanned or protected PDF?), skipping")
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
        log.info(f"  {meta['pages']} pages")
    return chunks


def index_pages(pages: list[dict], skip_existing: bool = False, replace_existing: bool = False) -> tuple[int, int]:
    """Index the HTML pages. Return (docs_ok, chunks_inserted).

    `replace_existing` is kept for compatibility: the per-chunk comparison
    (text and vector) applies in every mode, so even the full sync
    re-embeds only what changed and removes the source's stale chunks.
    """
    return _index_documents(pages, "HTML", _page_chunks, skip_existing)


def index_pdfs(pdfs: list[dict], skip_existing: bool = False, replace_existing: bool = False) -> tuple[int, int]:
    """Index the PDF documents. Return (docs_ok, chunks_inserted).
    See index_pages for the meaning of `replace_existing`."""
    return _index_documents(pdfs, "PDF", _pdf_chunks, skip_existing)


def main():
    parser = argparse.ArgumentParser(description="Indexer for the website content")
    parser.add_argument("--reset", action="store_true",
                        help="Empty the vector store before indexing")
    parser.add_argument("--stats", action="store_true",
                        help="Show statistics and exit")
    parser.add_argument("--only-html", action="store_true",
                        help="Index only the HTML pages")
    parser.add_argument("--only-pdf", action="store_true",
                        help="Index only the PDF documents")
    args = parser.parse_args()

    if args.stats:
        stats = get_stats()
        print(f"\nVector store: {stats['collection']}")
        print(f"Total chunks: {stats['total_chunks']}")
        return

    if args.reset:
        log.info("Resetting the vector store...")
        clear_collection()

    index = load_index()
    pages = index.get("pages", [])
    pdfs = index.get("pdfs", [])

    log.info(f"Found: {len(pages)} HTML pages, {len(pdfs)} PDF documents")
    log.info("Loading embedding model (first run: automatic download)...")

    total_docs = 0
    total_chunks = 0

    skip = not args.reset  # skip those already indexed unless --reset

    if not args.only_pdf:
        docs, chunks = index_pages(pages, skip_existing=skip)
        total_docs += docs
        total_chunks += chunks
        log.info(f"HTML: {docs}/{len(pages)} documents, {chunks} chunks")

    if not args.only_html:
        docs, chunks = index_pdfs(pdfs, skip_existing=skip)
        total_docs += docs
        total_chunks += chunks
        log.info(f"PDF: {docs}/{len(pdfs)} documents, {chunks} chunks")

    stats = get_stats()
    log.info("\n=== Indexing completed ===")
    log.info(f"Documents processed: {total_docs}")
    log.info(f"Chunks inserted this session: {total_chunks}")
    log.info(f"Total chunks in the vector store: {stats['total_chunks']}")


if __name__ == "__main__":
    main()
