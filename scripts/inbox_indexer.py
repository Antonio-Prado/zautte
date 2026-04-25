"""
Indicizzatore per documenti caricati manualmente nella cartella inbox.

Uso:
    python -m scripts.inbox_indexer            # processa i file in data/inbox/
    python -m scripts.inbox_indexer --watch    # rimane in ascolto (polling)

La cartella inbox accetta:
    - PDF  (.pdf)
    - Word (.docx)  — richiede python-docx
    - Testo (.txt)

Ogni file processato viene spostato in data/inbox/processed/
I file con errori vanno in data/inbox/errors/

Metadati opzionali: crea un file .json con lo stesso nome del documento.
Esempio: per "delibera_2024.pdf" crea "delibera_2024.json":
    {
        "title": "Delibera n.15 del 2024",
        "source_url": "https://www.mioente.it/documenti/2024/15",
        "category": "delibere"
    }
"""

import json
import logging
import shutil
import time
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import DATA_DIR
from indexer.pdf_extractor import extract_text_from_pdf
from indexer.chunker import chunk_document
from indexer.embedder import embed_texts
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
    """Carica metadati opzionali dal file .json accanto al documento."""
    meta_path = doc_path.with_suffix(".json")
    if meta_path.exists():
        try:
            with open(meta_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def extract_text(doc_path: Path) -> str:
    """Estrae il testo dal documento in base all'estensione."""
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
            log.error("python-docx non installato. Esegui: pip install python-docx")
            raise
        except Exception as e:
            log.error(f"Errore lettura DOCX: {e}")
            raise

    else:
        raise ValueError(f"Estensione non supportata: {ext}")


def process_file(doc_path: Path) -> bool:
    """
    Processa un singolo file: estrae testo, chunka, indicizza.
    Ritorna True se il processing è avvenuto con successo.
    """
    log.info(f"Processamento: {doc_path.name}")

    try:
        meta = load_metadata(doc_path)
        text = extract_text(doc_path)

        if not text or len(text.strip()) < 100:
            log.warning(f"  Testo troppo corto o vuoto, skip: {doc_path.name}")
            return False

        title = meta.get("title") or doc_path.stem
        source_url = meta.get("source_url") or f"file://{doc_path.name}"
        category = meta.get("category", "documento")

        chunks = chunk_document(
            text=text,
            source_url=source_url,
            title=title,
            doc_type="pdf" if doc_path.suffix.lower() == ".pdf" else "document",
            extra_metadata={"category": category, "filename": doc_path.name},
        )

        if not chunks:
            log.warning(f"  Nessun chunk generato: {doc_path.name}")
            return False

        embeddings = embed_texts([c["text"] for c in chunks])
        inserted = upsert_chunks(chunks, embeddings)

        log.info(f"  OK: {inserted} chunk indicizzati da '{title}'")
        return True

    except Exception as e:
        log.error(f"  ERRORE: {doc_path.name} — {e}", exc_info=True)
        return False


def process_inbox() -> tuple[int, int]:
    """
    Processa tutti i file supportati nella cartella inbox.
    Ritorna (ok, errori).
    """
    ensure_dirs()

    candidates = [
        f for f in INBOX_DIR.iterdir()
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXT
    ]

    if not candidates:
        log.info("Inbox vuota, nessun file da processare.")
        return 0, 0

    log.info(f"Trovati {len(candidates)} file nell'inbox.")
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

    log.info(f"Inbox: {ok} OK, {errors} errori")
    return ok, errors


def watch_inbox(interval_seconds: int = 60):
    """Polling continuo della cartella inbox."""
    log.info(f"Watch inbox attivo (ogni {interval_seconds}s). Ctrl+C per fermare.")
    try:
        while True:
            process_inbox()
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        log.info("Watch interrotto.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Inbox indexer — processa nuovi documenti")
    parser.add_argument("--watch", action="store_true",
                        help="Rimane in ascolto e processa nuovi file periodicamente")
    parser.add_argument("--interval", type=int, default=60,
                        help="Intervallo di polling in secondi (default: 60)")
    args = parser.parse_args()

    if args.watch:
        watch_inbox(args.interval)
    else:
        process_inbox()
