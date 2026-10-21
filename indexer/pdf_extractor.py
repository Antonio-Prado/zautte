"""
Text extraction from PDF documents using pypdf (pure Python, no compilation).
"""

import contextlib
import logging
import re
import signal
import threading
import time
from pathlib import Path

from config.settings import PDF_MAX_CHARS, PDF_MAX_PAGES, PDF_MAX_SECONDS

log = logging.getLogger(__name__)


class _TimedOut(Exception):
    pass


@contextlib.contextmanager
def _time_limit(seconds: int):
    """Interrupts a single page that never finishes (a hostile file can make the parser loop for
    hours inside one call). Needs SIGALRM and the main thread; elsewhere the check between pages remains."""
    if seconds <= 0 or not hasattr(signal, "setitimer") or threading.current_thread() is not threading.main_thread():
        yield
        return

    def _raise(signum, frame):
        raise _TimedOut

    previous = signal.signal(signal.SIGALRM, _raise)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def extract_text_from_pdf(pdf_path: str | Path) -> str:
    """
    Extract text from a PDF page by page.
    Return the concatenated text, or an empty string on error.
    """
    from pypdf import PdfReader

    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        log.warning(f"PDF not found: {pdf_path}")
        return ""

    try:
        started = time.monotonic()
        parts: list[str] = []
        chars = 0
        try:
            with _time_limit(PDF_MAX_SECONDS):
                reader = PdfReader(str(pdf_path))
                for page_num, page in enumerate(reader.pages, start=1):
                    if page_num > PDF_MAX_PAGES:
                        log.warning(f"PDF {pdf_path.name}: only the first {PDF_MAX_PAGES} pages are read")
                        break
                    if time.monotonic() - started > PDF_MAX_SECONDS:
                        raise _TimedOut
                    text = page.extract_text() or ""
                    if text.strip():
                        parts.append(f"[Pagina {page_num}]\n{text.strip()}")
                        chars += len(parts[-1])
                    if chars > PDF_MAX_CHARS:
                        log.warning(f"PDF {pdf_path.name}: text cut at {PDF_MAX_CHARS} characters")
                        break
        except _TimedOut:
            log.warning(f"PDF {pdf_path.name}: reading stopped after {PDF_MAX_SECONDS} s, "
                        f"{len(parts)} pages kept")

        full_text = "\n\n".join(parts)[:PDF_MAX_CHARS]
        return _clean_pdf_text(full_text)

    except Exception as e:  # noqa: BLE001 - pypdf raises heterogeneous exception types on corrupt PDFs
        log.error(f"PDF extraction error {pdf_path.name}: {e}")
        return ""


def _clean_pdf_text(text: str) -> str:
    # Remove null bytes and control characters (except \t \n \r)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    # Remove the Unicode replacement character and surrogates
    text = re.sub(r"[\ufffd\ufffe\uffff]", "", text)
    text = re.sub(r"[\ud800-\udfff]", "", text)
    # Remove very long "words" without spaces (base64, hashes, binary)
    text = re.sub(r"\S{200,}", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines = [line for line in text.splitlines() if re.search(r"[a-zA-ZÀ-ÿ0-9]", line)]
    return "\n".join(lines).strip()


def _meta_str(val) -> str:
    """Convert a PDF metadata value to a string (pypdf may return bytes)."""
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="replace").strip()
    if val is None:
        return ""
    return str(val).strip()


def get_pdf_metadata(pdf_path: str | Path) -> dict:
    from pypdf import PdfReader

    pdf_path = Path(pdf_path)
    try:
        reader = PdfReader(str(pdf_path))
        meta = reader.metadata or {}
        return {
            "title": _meta_str(meta.get("/Title")) or pdf_path.stem,
            "author": _meta_str(meta.get("/Author")),
            "pages": len(reader.pages),
        }
    except Exception:  # noqa: BLE001 - pypdf raises heterogeneous exception types on corrupt PDFs
        return {"title": pdf_path.stem, "author": "", "pages": 0}
