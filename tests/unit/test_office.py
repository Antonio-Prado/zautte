"""Office attachments (indexer/office.py): spreadsheets, documents and archives read without libraries."""
import io
import zipfile

from indexer import office

PDF_TEXT = lambda data, n: "PDF:" + data[:8].decode("latin-1")


def zipped(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


XLSX = zipped({
    "xl/workbook.xml": b'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                       b'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                       b'<sheets><sheet name="Pagamenti" sheetId="1" r:id="rId1"/></sheets></workbook>',
    "xl/_rels/workbook.xml.rels": b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                  b'<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
    "xl/sharedStrings.xml": b'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                            b'<si><t>Beneficiario</t></si><si><t>Importo</t></si><si><t>Rossi srl</t></si></sst>',
    "xl/worksheets/sheet1.xml": b'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
                                b'<row><c t="s"><v>0</v></c><c t="s"><v>1</v></c></row>'
                                b'<row><c t="s"><v>2</v></c><c><v>1250.5</v></c></row>'
                                b'<row><c t="inlineStr"><is><t>Totale</t></is></c><c><v>1250.5</v></c></row>'
                                b'</sheetData></worksheet>',
})
DOCX = zipped({"word/document.xml": b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                                    b'<w:body><w:p><w:r><w:t>Relazione </w:t></w:r><w:r><w:t>annuale</w:t></w:r></w:p>'
                                    b'<w:p><w:r><w:t>Secondo paragrafo</w:t></w:r></w:p></w:body></w:document>'})
ODS = zipped({"content.xml": b'<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
                             b'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
                             b'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"><office:body><office:spreadsheet>'
                             b'<table:table table:name="Dati"><table:table-row><table:table-cell><text:p>Voce</text:p></table:table-cell>'
                             b'<table:table-cell table:number-columns-repeated="2"><text:p>x</text:p></table:table-cell></table:table-row>'
                             b'<table:table-row table:number-rows-repeated="1000"><table:table-cell/></table:table-row>'
                             b'</table:table></office:spreadsheet></office:body></office:document-content>'})
ODT = zipped({"content.xml": b'<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
                             b'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"><office:body><office:text>'
                             b'<text:h>Titolo</text:h><text:p>Un <text:span>paragrafo</text:span>.</text:p>'
                             b'</office:text></office:body></office:document-content>'})


def test_kind_strips_signatures_and_tokens():
    assert office.kind("Bilancio.pdf.p7m") == "pdf"
    assert office.kind("dati.XLSX") == "xlsx"
    assert office.kind("elenco.csv?token=1") == "csv"
    assert office.kind("foto.jpg") == "image"            # read with OCR since 0.40.0
    assert office.kind("archivio.rar") == ""
    assert office.kind("vecchio.doc") == "doc"          # read since 0.36.0
    assert office.kind("vecchio.rtf") == ""
    assert office.readable("allegati.zip") and office.readable("x.xls") and not office.readable("x.rtf")


def test_xlsx_rows_with_shared_and_inline_strings():
    text = office.extract("pagamenti.xlsx", XLSX, 10000, PDF_TEXT)
    assert text == "[Foglio Pagamenti]\nBeneficiario\tImporto\nRossi srl\t1250.5\nTotale\t1250.5"


def test_docx_odt_ods():
    assert office.extract("relazione.docx", DOCX, 10000, PDF_TEXT) == "Relazione annuale\nSecondo paragrafo"
    assert office.extract("nota.odt", ODT, 10000, PDF_TEXT) == "Titolo\nUn paragrafo."
    assert office.extract("dati.ods", ODS, 10000, PDF_TEXT) == "[Foglio Dati]\nVoce\tx\tx"   # repeated empty rows skipped


def test_csv_sniffs_the_separator_and_encoding():
    data = "Nome;Importo\nCaffè;12,50\n".encode("cp1252")
    assert office.extract("x.csv", data, 10000, PDF_TEXT) == "Nome\tImporto\nCaffè\t12,50"


def test_zip_reads_its_members_once_and_pdfs_go_to_the_pdf_reader():
    inner = zipped({"nested.pdf": b"%PDF-1.4 nested"})
    data = zipped({"a/doc.pdf": b"%PDF-1.4 abc", "b.docx": DOCX, "img.png": b"\x89PNG", "deep.zip": inner})
    text = office.extract("allegati.zip", data, 10000, PDF_TEXT)
    assert "[doc.pdf]\nPDF:%PDF-1.4" in text and "[b.docx]\nRelazione annuale" in text
    assert "img.png" not in text and "nested" not in text   # unreadable and nested archives are left out


def test_limits_and_garbage():
    assert len(office.extract("big.docx", DOCX, 5, PDF_TEXT)) == 5
    assert office.extract("x.xlsx", b"not a zip", 100, PDF_TEXT) == ""
    assert office.extract("x.docx", zipped({"other.xml": b"<a/>"}), 100, PDF_TEXT) == ""
    assert office.extract("x.ods", zipped({"content.xml": b"<broken"}), 100, PDF_TEXT) == ""
    assert office.extract("x.pdf", b"%PDF-1.7 ok", 100, PDF_TEXT) == "PDF:%PDF-1.7"
    assert office.extract("x.png", b"\x89PNG", 100, PDF_TEXT) == ""
