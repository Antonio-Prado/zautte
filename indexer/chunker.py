"""
Divisione dei testi in chunk ottimizzati per RAG.

Strategia:
  1. Split semantico per paragrafo (\n\n) — mantiene unità di senso intere
  2. Se un paragrafo supera MAX_PARAGRAPH_CHARS, lo taglia ulteriormente
     con overlap per non perdere contesto ai bordi
  3. Il titolo della pagina viene preposto ad ogni chunk per migliorare
     il retrieval anche quando il testo non contiene parole chiave esplicite
  4. Righe rumorose (navigazione, feedback, pulsanti) vengono rimosse
"""

import re
from langchain_text_splitters import RecursiveCharacterTextSplitter

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import CHUNK_SIZE, CHUNK_OVERLAP

# Paragrafi più lunghi di questo vengono ulteriormente suddivisi
MAX_PARAGRAPH_CHARS = 1200

# Overlap aumentato per non spezzare concetti ai bordi
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
    """Rimuove righe rumorose dal testo di un chunk."""
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
    """Suddivide un paragrafo lungo con overlap."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=MAX_PARAGRAPH_CHARS,
        chunk_overlap=EFFECTIVE_OVERLAP,
        separators=[". ", ", ", " ", ""],
        keep_separator=True,
    )
    return splitter.split_text(text)


def chunk_document(
    text: str,
    source_url: str,
    title: str,
    doc_type: str = "html",  # "html" | "pdf"
    extra_metadata: dict | None = None,
) -> list[dict]:
    """
    Divide un documento in chunk, ognuno con i suoi metadati.

    Ritorna lista di dict:
    {
        "text": str,        # titolo + testo del chunk
        "metadata": {
            "source": str,
            "title": str,
            "doc_type": str,
            "chunk_index": int,
            ...extra_metadata
        }
    }
    """
    text = text.strip()
    if not text:
        return []

    # --- Split semantico per paragrafo ---
    paragraphs = re.split(r"\n{2,}", text)

    raw_chunks: list[str] = []
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(para) <= MAX_PARAGRAPH_CHARS:
            raw_chunks.append(para)
        else:
            # Paragrafo troppo lungo: suddividi con overlap
            raw_chunks.extend(_split_long_paragraph(para))

    chunks = []
    for i, chunk_text in enumerate(raw_chunks):
        chunk_text = _clean_chunk(chunk_text)
        if len(chunk_text) < 80:
            continue

        # Prependi il titolo per migliorare il retrieval semantico
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
