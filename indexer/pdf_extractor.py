"""
Text extraction from PDF documents using pypdf (pure Python, no compilation).
"""

import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)


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
        reader = PdfReader(str(pdf_path))
        parts = []
        for page_num, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            if text.strip():
                parts.append(f"[Pagina {page_num}]\n{text.strip()}")

        full_text = "\n\n".join(parts)
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
