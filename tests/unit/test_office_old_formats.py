"""Word 97-2003 (.doc) and Excel 97-2003 (.xls) attachments (indexer/office.py)."""
import io
import struct

import pytest

from indexer import office

OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _word_file(text8: str, text16: str, ccp_text: int | None = None) -> tuple[bytes, bytes]:
    """A WordDocument stream (FIB + the text runs) and a table stream (the Clx) with two pieces:
    an 8-bit run and a 16-bit run."""
    fib = bytearray(0x200)
    body8 = text8.encode("cp1252")
    body16 = text16.encode("utf-16-le")
    word = bytes(fib) + body8 + body16
    fc8 = 0x200 * 2 | 0x40000000          # compressed: the file offset doubled, flag set
    fc16 = 0x200 + len(body8)
    n = 2
    cps = [0, len(text8), len(text8) + len(text16)]
    plc = b"".join(struct.pack("<I", cp) for cp in cps)
    plc += struct.pack("<HIH", 0, fc8, 0) + struct.pack("<HIH", 0, fc16, 0)
    prc = b"\x01" + struct.pack("<H", 2) + b"\x00\x00"      # a property modifier before the Pcdt
    clx = prc + b"\x02" + struct.pack("<I", len(plc)) + plc
    table = b"\x00" * 16 + clx
    word = bytearray(word)
    word[0x01A2:0x01A6] = struct.pack("<I", 16)
    word[0x01A6:0x01AA] = struct.pack("<I", len(clx))
    word[0x4C:0x50] = struct.pack("<I", ccp_text if ccp_text is not None else cps[-1])
    word[0x0A:0x0C] = struct.pack("<H", 0x0200)              # 1Table
    assert n == 2
    return bytes(word), table


def test_doc_pieces_text_reads_both_kinds_of_runs():
    word, table = _word_file("Regolamento\rArt. 1 - Oggetto\r", "Il Comitato è composto da:\x07cella\x07\r")
    text = office.doc_pieces_text(word, table)
    assert text == "Regolamento\nArt. 1 - Oggetto\nIl Comitato è composto da:\tcella\t\n"


def test_doc_pieces_text_stops_at_the_main_text():
    word, table = _word_file("Testo principale\r", "Nota a piè di pagina\r", ccp_text=len("Testo principale\r"))
    assert office.doc_pieces_text(word, table) == "Testo principale\n"


def test_doc_pieces_text_drops_field_codes_and_pictures():
    word, table = _word_file("Vedi \x13 HYPERLINK \"x\" \x14sito\x15 e \x01immagine\r", "")
    assert office.doc_pieces_text(word, table) == "Vedi sito e immagine\n"


def test_doc_pieces_text_rejects_a_broken_fib():
    with pytest.raises(ValueError):
        office.doc_pieces_text(b"\x00" * 0x200, b"\x00" * 32)


def test_kind_and_extract_of_the_old_formats():
    assert office.kind("Modello osservazioni.doc") == "doc"
    assert office.kind("Organi 2020(.xls).xls") == "xls"
    assert office.kind("vecchio.doc.p7m") == "doc"
    assert office.readable("tabella.xls")
    # not an OLE file: nothing (an RTF or an HTML page saved with the old extension)
    assert office.extract("x.doc", b"{\\rtf1 ciao}", 1000, lambda d, m: "") == ""
    assert office.extract("x.xls", b"<html><table>", 1000, lambda d, m: "") == ""
    # an OLE file without a Word stream: nothing, no exception
    assert office.extract("x.doc", OLE + b"\x00" * 600, 1000, lambda d, m: "") == ""


def test_xls_through_xlrd(monkeypatch):
    class Cell:
        def __init__(self, ctype, value):
            self.ctype, self.value = ctype, value

    class Sheet:
        name = "Compensi"
        nrows = 2

        def row(self, r):
            return [[Cell(1, "Nome"), Cell(1, "Importo")], [Cell(1, "Rossi"), Cell(2, 51272.31)]][r]

    class Book:
        datemode = 0

        def sheets(self):
            return [Sheet()]

    import xlrd
    monkeypatch.setattr(xlrd, "open_workbook", lambda file_contents, on_demand: Book())
    text = office.extract("compensi.xls", OLE + b"\x00" * 600, 1000, lambda d, m: "")
    assert text == "[Compensi]\nNome\tImporto\nRossi\t51272.31"
    assert io is not None
