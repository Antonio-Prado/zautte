"""
Text extraction from PDF documents using pypdf (pure Python, no compilation).
"""

import contextlib
import logging
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from config.settings import (
    PDF_MAX_CHARS,
    PDF_MAX_PAGES,
    PDF_MAX_SECONDS,
    PDF_OCR,
    PDF_OCR_DPI,
    PDF_OCR_LANG,
    PDF_OCR_MAX_PAGES,
    PDF_OCR_MAX_SECONDS,
)

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
        if not full_text.strip():
            full_text = _fallback_text(pdf_path, "no text")
        return _with_ocr(pdf_path, _clean_pdf_text(full_text))

    except Exception as e:  # noqa: BLE001 - pypdf raises heterogeneous exception types on corrupt PDFs
        text = _with_ocr(pdf_path, _clean_pdf_text(_fallback_text(pdf_path, str(e))))
        if not text:
            log.error(f"PDF extraction error {pdf_path.name}: {e}")
        return text


def _fallback_text(pdf_path: Path, reason: str) -> str:
    """The text through pdfminer, when pypdf gave up or found nothing: pypdf stops on a content
    stream with too many tokens («Reached maximum number of tokens per stream», a large drawing), on
    a stream above its decompression limit or on a malformed object, and pdfminer, slower, reads
    them. "" when pdfminer is not installed or fails too."""
    try:
        from pdfminer.high_level import extract_text
    except ImportError:
        return ""
    try:
        with _time_limit(PDF_MAX_SECONDS):
            raw = extract_text(str(pdf_path), maxpages=PDF_MAX_PAGES) or ""
    except _TimedOut:
        log.warning(f"PDF {pdf_path.name}: pdfminer stopped after {PDF_MAX_SECONDS} s")
        return ""
    except Exception as e:  # noqa: BLE001 - pdfminer raises its own heterogeneous exceptions
        log.warning(f"PDF {pdf_path.name}: pdfminer failed too ({e})")
        return ""
    parts = []
    for n, page in enumerate(raw.split("\f"), start=1):
        if page.strip():
            parts.append(f"[Pagina {n}]\n{page.strip()}")
    text = "\n\n".join(parts)[:PDF_MAX_CHARS]
    if text.strip():
        log.info(f"PDF {pdf_path.name}: read with pdfminer ({reason[:60]})")
    return text


# --------------------------------------------------------------------------- OCR

_GOOD_CHARS = re.compile(r"[\w\s.,;:'’\"«»()\-–/%€&+*=§°º]", re.UNICODE)
_LETTERS = re.compile(r"[^\W\d_]", re.UNICODE)


def legible(text: str) -> bool:
    """Whether the text a reader gave is text at all: at least twenty letters, and no more than a
    fifth of odd characters. A scan gives nothing; a PDF whose fonts have no Unicode map (PDF24 with
    an old Ghostscript) gives control codes and symbols in place of the letters."""
    stripped = text.strip()
    if len(_LETTERS.findall(stripped)) < 20:
        return False
    chars = [c for c in stripped if not c.isspace()]
    bad = sum(1 for c in chars if not _GOOD_CHARS.match(c))
    return bad <= 0.2 * len(chars)


def ocr_available() -> bool:
    """pdftoppm (poppler-utils) and tesseract are installed, and the setting allows them."""
    if PDF_OCR == "off":
        return False
    found = bool(shutil.which("pdftoppm") and shutil.which("tesseract"))
    if PDF_OCR == "on" and not found:
        log.warning("PDF_OCR=on but pdftoppm or tesseract is missing: no OCR")
    return found


def _with_ocr(pdf_path: Path, text: str) -> str:
    """The text as read, or the OCR of the pages when that text is not legible and OCR is available."""
    if legible(text) or not ocr_available():
        return text
    recognised = _clean_pdf_text(ocr_text(pdf_path))
    if legible(recognised):
        log.info(f"PDF {pdf_path.name}: read with OCR ({len(recognised)} characters)")
        return recognised
    return text


def ocr_text(pdf_path: Path, max_pages: int = PDF_OCR_MAX_PAGES, dpi: int = PDF_OCR_DPI,
             lang: str = PDF_OCR_LANG, max_seconds: int = PDF_OCR_MAX_SECONDS) -> str:
    """The pages rendered with pdftoppm and read with tesseract, «[Pagina n]» before each; what was
    read within `max_seconds` is kept."""
    started = time.monotonic()
    parts: list[str] = []
    try:
        with tempfile.TemporaryDirectory(prefix="ocr-") as tmp:
            prefix = str(Path(tmp) / "pg")
            subprocess.run(["pdftoppm", "-r", str(dpi), "-gray", "-png", "-f", "1", "-l", str(max_pages),
                            str(pdf_path), prefix], check=True, capture_output=True, timeout=max_seconds)
            pages = sorted(Path(tmp).glob("pg-*.png"))
            for n, page in enumerate(pages, start=1):
                left = max_seconds - (time.monotonic() - started)
                if left <= 1:
                    log.warning(f"PDF {pdf_path.name}: OCR stopped after {max_seconds} s, {n - 1} pages kept")
                    break
                out = subprocess.run(["tesseract", str(page), "stdout", "-l", lang], check=True,
                                     capture_output=True, timeout=left)
                page_text = out.stdout.decode("utf-8", errors="replace").strip()
                if page_text:
                    parts.append(f"[Pagina {n}]\n{page_text}")
            if len(pages) >= max_pages:
                log.info(f"PDF {pdf_path.name}: OCR of the first {max_pages} pages only")
    except subprocess.TimeoutExpired:
        log.warning(f"PDF {pdf_path.name}: OCR stopped after {max_seconds} s, {len(parts)} pages kept")
    except (subprocess.CalledProcessError, OSError) as e:
        log.warning(f"PDF {pdf_path.name}: OCR failed ({str(e)[:120]})")
    return "\n\n".join(parts)[:PDF_MAX_CHARS]


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
