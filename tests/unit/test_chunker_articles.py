"""Long paragraphs are cut at line ends and sentence ends, never between «Art.» and its number
(indexer/chunker.py)."""
from indexer import chunker


def _long_regulation() -> str:
    body = ("Il presente regolamento disciplina la tutela degli animali nel territorio comunale e stabilisce "
            "gli obblighi dei detentori, le sanzioni e i compiti degli uffici competenti in materia. ")
    parts = []
    for n in range(38, 46):
        parts.append(f"Art. {n} - Titolo dell'articolo numero {n}\n1. " + body * 3)
    return "\n".join(parts)


def test_article_headings_are_not_cut_from_their_number():
    text = _long_regulation()
    assert len(text) > 3 * chunker.MAX_PARAGRAPH_CHARS
    pieces = chunker._split_long_paragraph(text)
    assert len(pieces) >= 3
    for piece in pieces:
        assert not piece.lstrip().startswith(". ")          # the old cut: «. 41 - Cura…»
        assert not piece.rstrip().endswith(("Art", "art"))    # «Art» left alone at the end
    joined = " ".join(pieces)
    for n in range(38, 46):
        assert f"Art. {n} - Titolo" in joined
    # every article heading starts a line in some piece, so article_passages can find it
    assert any(line.startswith("Art. 41 - Titolo") for piece in pieces for line in piece.splitlines())


def test_sentence_end_stays_with_its_sentence():
    text = ("Una frase di prova abbastanza lunga da contare qualcosa. " * 40).strip()
    pieces = chunker._split_long_paragraph(text)
    assert len(pieces) > 1
    assert all(p.rstrip().endswith(".") for p in pieces)
    assert all(not p.startswith(". ") for p in pieces)


def test_act_numbers_are_not_cut():
    text = ("La determina n. 1200 del 2025 e la delibera n. 45 approvano il piano. " * 30).strip()
    for piece in chunker._split_long_paragraph(text):
        assert not piece.rstrip().endswith(" n") and not piece.lstrip().startswith(". 1200")


def test_chunk_document_keeps_lines_of_a_regulation_read_by_ocr():
    text = "Regolamento\n\n" + _long_regulation()
    chunks = chunker.chunk_document(text, "https://example.org/r.pdf", "Regolamento", doc_type="pdf")
    assert len(chunks) >= 3
    assert any("\nArt. 41 - Titolo" in c["text"] for c in chunks)
    assert all(c["metadata"]["doc_type"] == "pdf" for c in chunks)
