"""
Splitting of texts into chunks optimized for RAG.

Strategy:
  1. Semantic split by paragraph (\n\n) — keeps whole units of meaning together
  2. If a paragraph exceeds MAX_PARAGRAPH_CHARS, it is split further
     with overlap so context is not lost at the edges
  3. The page title is prepended to every chunk to improve retrieval
     even when the text does not contain explicit keywords
  4. Noisy lines (navigation, feedback, buttons) are removed
"""

import re
import sys
import unicodedata
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import CHUNK_OVERLAP

# Paragraphs longer than this are split further
MAX_PARAGRAPH_CHARS = 1200

# Increased overlap so concepts are not cut at the edges
EFFECTIVE_OVERLAP = max(CHUNK_OVERLAP, 150)

_NOISE_CHUNK_LINES = re.compile(
    r"^(vai alla pagina|leggi di più|leggi di piu|municipium|"
    r"accedi al servizio|con identità digitale|con spid|con cie|"
    r"scarica|download|allega|stampa|condividi|"
    r"le indicazioni erano|ho avuto problemi|a volte le indicazioni|"
    r"capivo sempre|non ho avuto problemi|dove hai incontrato|"
    r"quali sono stati|altro|\d+/\d+)$",
    re.IGNORECASE
)


def _clean_chunk(text: str) -> str:
    """Remove noisy lines from a chunk's text."""
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line or len(line) < 4:
            continue
        if _NOISE_CHUNK_LINES.match(line):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _split_long_paragraph(text: str) -> list[str]:
    """Split a long paragraph with overlap."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=MAX_PARAGRAPH_CHARS,
        chunk_overlap=EFFECTIVE_OVERLAP,
        separators=[". ", ", ", " ", ""],
        keep_separator=True,
    )
    return splitter.split_text(text)


def strip_invisible_chars(text: str) -> str:
    """Removes the characters a reader cannot see but a model reads: format controls (zero-width,
    bidirectional marks, the 'tag' block U+E0000 that can spell a hidden message in ASCII), private
    use and unassigned code points, and control characters other than newline and tab."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(
        ch for ch in text
        if ch in "\n\t" or (unicodedata.category(ch) not in ("Cf", "Co", "Cs", "Cn", "Cc") and ch != "\ufeff")
    )


def chunk_document(
    text: str,
    source_url: str,
    title: str,
    doc_type: str = "html",  # "html" | "pdf"
    extra_metadata: dict | None = None,
) -> list[dict]:
    """
    Split a document into chunks, each with its metadata.

    Return a list of dicts:
    {
        "text": str,        # title + chunk text
        "metadata": {
            "source": str,
            "title": str,
            "doc_type": str,
            "chunk_index": int,
            ...extra_metadata
        }
    }
    """
    text = strip_invisible_chars(text).strip()
    title = re.sub(r"\s+", " ", strip_invisible_chars(title or "")).strip()[:200]
    if not text:
        return []

    # --- Semantic split by paragraph ---
    paragraphs = re.split(r"\n{2,}", text)

    raw_chunks: list[str] = []
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(para) <= MAX_PARAGRAPH_CHARS:
            raw_chunks.append(para)
        else:
            # Paragraph too long: split with overlap
            raw_chunks.extend(_split_long_paragraph(para))

    chunks = []
    for i, chunk_text in enumerate(raw_chunks):
        chunk_text = _clean_chunk(chunk_text)
        if len(chunk_text) < 80:
            continue

        # Prepend the title to improve semantic retrieval
        titled_text = f"{title}\n\n{chunk_text}" if title else chunk_text

        metadata = {
            "source": source_url,
            "title": title,
            "doc_type": doc_type,
            "chunk_index": i,
            "chunk_total": len(raw_chunks),
        }
        if extra_metadata:
            metadata.update(extra_metadata)

        chunks.append({"text": titled_text, "metadata": metadata})

    return chunks
