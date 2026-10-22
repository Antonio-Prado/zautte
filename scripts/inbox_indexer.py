"""
Indexer for documents manually uploaded to the inbox folder.

Usage:
    python -m scripts.inbox_indexer            # process the files in data/inbox/
    python -m scripts.inbox_indexer --watch    # keep listening (polling)

The inbox folder accepts:
    - PDF  (.pdf)
    - Word (.docx)  — requires python-docx
    - Text (.txt)

Each processed file is moved to data/inbox/processed/
Files with errors go to data/inbox/errors/

Optional metadata: create a .json file with the same name as the document.
Example: for "delibera_2024.pdf" create "delibera_2024.json":
    {
        "title": "Delibera n.15 del 2024",
        "source_url": "https://www.mioente.it/documenti/2024/15",
        "category": "delibere"
    }
"""

import json
import logging
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from api.privdrop import drop_to_sync_user

drop_to_sync_user()   # as root: become the sync account before config.settings reads .env

from config.settings import DATA_DIR
from indexer.chunker import chunk_document
from indexer.embedder import embed_texts
from indexer.pdf_extractor import extract_text_from_pdf
from indexer.vector_store import upsert_chunks

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

INBOX_DIR     = DATA_DIR / "inbox"
PROCESSED_DIR = INBOX_DIR / "processed"
ERRORS_DIR    = INBOX_DIR / "errors"
SUPPORTED_EXT = {".pdf", ".txt", ".docx"}


def ensure_dirs():
    for d in [INBOX_DIR, PROCESSED_DIR, ERRORS_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def load_metadata(doc_path: Path) -> dict:
    """Load optional metadata from the .json file next to the document."""
    meta_path = doc_path.with_suffix(".json")
    if meta_path.exists():
        try:
            with open(meta_path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError) as e:
            log.warning(f"Metadata {meta_path.name} unreadable: {e}")
    return {}


def extract_text(doc_path: Path) -> str:
    """Extract the text from the document based on its extension."""
    ext = doc_path.suffix.lower()

    if ext == ".pdf":
        return extract_text_from_pdf(doc_path)

    elif ext == ".txt":
        try:
            return doc_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return doc_path.read_text(encoding="latin-1")

    elif ext == ".docx":
        try:
            import docx
            doc = docx.Document(str(doc_path))
            return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())
        except ImportError:
            log.error("python-docx not installed. Run: pip install python-docx")
            raise
        except Exception as e:
            log.error(f"Error reading DOCX: {e}")
            raise

    else:
        raise ValueError(f"Estensione non supportata: {ext}")


def process_file(doc_path: Path) -> bool:
    """
    Process a single file: extract text, chunk, index.
    Returns True if processing succeeded.
    """
    log.info(f"Processing: {doc_path.name}")

    try:
        meta = load_metadata(doc_path)
        text = extract_text(doc_path)

        if not text or len(text.strip()) < 100:
            log.warning(f"  Text too short or empty, skipping: {doc_path.name}")
            return False

        title = meta.get("title") or doc_path.stem
        source_url = meta.get("source_url") or f"file://{doc_path.name}"
        if not source_url.lower().startswith(("http://", "https://", "file://")):
            log.warning(f"  source_url with an unexpected scheme replaced: {doc_path.name}")
            source_url = f"file://{doc_path.name}"
        category = meta.get("category", "documento")

        chunks = chunk_document(
            text=text,
            source_url=source_url,
            title=title,
            doc_type="pdf" if doc_path.suffix.lower() == ".pdf" else "document",
            # origin=inbox: the vector store does not remove them as "stale" in syncs
            # (they are not in the crawl) nor replace them with the crawled page that
            # has the same source_url. Without this marker every full sync
            # deleted them all (14/09/2026: zero inbox documents in the VS).
            extra_metadata={"category": category, "filename": doc_path.name, "origin": "inbox"},
        )

        if not chunks:
            log.warning(f"  No chunks generated: {doc_path.name}")
            return False

        embeddings = embed_texts([c["text"] for c in chunks])
        inserted = upsert_chunks(chunks, embeddings)

        log.info(f"  OK: {inserted} chunks indexed from '{title}'")
        return True

    except Exception:
        log.exception(f"  ERROR: {doc_path.name}")
        return False


def process_inbox() -> tuple[int, int]:
    """
    Process all the supported files in the inbox folder.
    Returns (ok, errors).
    """
    ensure_dirs()

    candidates = [
        f for f in INBOX_DIR.iterdir()
        if f.is_file() and not f.is_symlink() and f.suffix.lower() in SUPPORTED_EXT
    ]
    for f in INBOX_DIR.iterdir():
        if f.is_symlink():
            log.warning(f"Link ignored in the inbox: {f.name}")

    if not candidates:
        log.info("Inbox empty, no files to process.")
        return 0, 0

    log.info(f"Found {len(candidates)} files in the inbox.")
    ok = 0
    errors = 0

    for doc_path in sorted(candidates):
        success = process_file(doc_path)
        meta_path = doc_path.with_suffix(".json")

        dest_dir = PROCESSED_DIR if success else ERRORS_DIR
        shutil.move(str(doc_path), dest_dir / doc_path.name)
        if meta_path.exists():
            shutil.move(str(meta_path), dest_dir / meta_path.name)

        if success:
            ok += 1
        else:
            errors += 1

    log.info(f"Inbox: {ok} OK, {errors} errors")
    return ok, errors


def watch_inbox(interval_seconds: int = 60):
    """Continuous polling of the inbox folder."""
    log.info(f"Inbox watch active (every {interval_seconds}s). Ctrl+C to stop.")
    try:
        while True:
            process_inbox()
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        log.info("Watch stopped.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Inbox indexer — process new documents")
    parser.add_argument("--watch", action="store_true",
                        help="Keep running and process new files periodically")
    parser.add_argument("--interval", type=int, default=60,
                        help="Polling interval in seconds (default: 60)")
    args = parser.parse_args()

    if args.watch:
        watch_inbox(args.interval)
    else:
        process_inbox()
