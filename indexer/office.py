"""
Text of the office files an administration attaches next to its PDFs: spreadsheets (.xlsx, .ods,
.csv), word processor files (.docx, .odt) and archives (.zip, opened for the files they hold).

Office Open XML (.xlsx, .docx) and OpenDocument (.ods, .odt) are ZIP archives of XML: the text is
read from the XML with the standard library, no office library needed. A spreadsheet becomes one
line per row, cells separated by a tab, sheets one after the other with their name; a document is
its paragraphs. Older binary formats (.xls, .doc) are not read, nor are images.

`extract(name, data, max_chars, pdf_text)` picks the reader from the file name, `.p7m` removed
(a signed office file is opened like a signed PDF); PDFs are handed to `pdf_text`, so a ZIP of PDFs
is read as well. Anything unreadable gives "". Archives are bounded: at most `_ZIP_MEMBERS` files,
`_ZIP_MEMBER_BYTES` each, one level of nesting, so a hostile archive cannot blow up the job.
"""
import csv
import io
import logging
import re
import zipfile
from collections.abc import Callable
from xml.etree import ElementTree as ET

log = logging.getLogger(__name__)

_ZIP_MEMBERS = 50
_ZIP_MEMBER_BYTES = 60 * 1024 * 1024
_NS_SS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_NS_WP = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_NS_TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
_NS_TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
_NS_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"

PdfText = Callable[[bytes, int], str]


def _tidy(text: str) -> str:
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _open_zip(data: bytes) -> zipfile.ZipFile | None:
    try:
        return zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError, OSError):
        return None


def _member(z: zipfile.ZipFile, name: str) -> bytes | None:
    try:
        info = z.getinfo(name)
    except KeyError:
        return None
    if info.file_size > _ZIP_MEMBER_BYTES:
        return None
    try:
        return z.read(name)
    except (zipfile.BadZipFile, RuntimeError, OSError, EOFError):
        return None


def _parse(data: bytes | None) -> ET.Element | None:
    if not data:
        return None
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        return None


# --------------------------------------------------------------------------- Office Open XML

def _xlsx_text(z: zipfile.ZipFile, max_chars: int) -> str:
    strings: list[str] = []
    shared = _parse(_member(z, "xl/sharedStrings.xml"))
    if shared is not None:
        for si in shared.iter(f"{_NS_SS}si"):
            strings.append("".join(t.text or "" for t in si.iter(f"{_NS_SS}t")))
    sheet_names: dict[str, str] = {}
    workbook = _parse(_member(z, "xl/workbook.xml"))
    rels = _parse(_member(z, "xl/_rels/workbook.xml.rels"))
    targets = {}
    if rels is not None:
        for rel in rels.iter(f"{_NS_PKG_REL}Relationship"):
            target = rel.get("Target", "")
            targets[rel.get("Id")] = target if target.startswith("/") else "xl/" + target
    if workbook is not None:
        for sheet in workbook.iter(f"{_NS_SS}sheet"):
            path = targets.get(sheet.get(f"{_NS_REL}id"), "")
            sheet_names[path.lstrip("/")] = sheet.get("name", "")
    out: list[str] = []
    size = 0
    for path in sorted(n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n)):
        root = _parse(_member(z, path))
        if root is None:
            continue
        title = sheet_names.get(path, "")
        if title:
            out.append(f"[Foglio {title}]")
        for row in root.iter(f"{_NS_SS}row"):
            cells = []
            for c in row.iter(f"{_NS_SS}c"):
                v = c.find(f"{_NS_SS}v")
                kind = c.get("t")
                if kind == "s" and v is not None and v.text and v.text.isdigit():
                    idx = int(v.text)
                    cells.append(strings[idx] if idx < len(strings) else "")
                elif kind == "inlineStr":
                    cells.append("".join(t.text or "" for t in c.iter(f"{_NS_SS}t")))
                else:
                    cells.append(v.text if v is not None and v.text else "")
            line = "\t".join(cells).rstrip()
            if line.strip():
                out.append(line)
                size += len(line) + 1
                if size >= max_chars:
                    return "\n".join(out)[:max_chars]
    return "\n".join(out)[:max_chars]


def _docx_text(z: zipfile.ZipFile, max_chars: int) -> str:
    root = _parse(_member(z, "word/document.xml"))
    if root is None:
        return ""
    paragraphs = []
    for p in root.iter(f"{_NS_WP}p"):
        text = "".join(t.text or "" for t in p.iter(f"{_NS_WP}t"))
        paragraphs.append(text)
    return _tidy("\n".join(paragraphs))[:max_chars]


# --------------------------------------------------------------------------- OpenDocument

def _odf_cell_text(cell: ET.Element) -> str:
    return " ".join("".join(p.itertext()).strip() for p in cell.iter(f"{_NS_TEXT}p")).strip()


def _ods_text(z: zipfile.ZipFile, max_chars: int) -> str:
    root = _parse(_member(z, "content.xml"))
    if root is None:
        return ""
    out: list[str] = []
    size = 0
    for table in root.iter(f"{_NS_TABLE}table"):
        name = table.get(f"{_NS_TABLE}name", "")
        if name:
            out.append(f"[Foglio {name}]")
        for row in table.iter(f"{_NS_TABLE}table-row"):
            repeat = int(row.get(f"{_NS_TABLE}number-rows-repeated", "1") or 1)
            if repeat > 50:            # thousands of empty rows repeated at the end of a sheet
                continue
            cells = []
            for cell in row:
                if cell.tag not in (f"{_NS_TABLE}table-cell", f"{_NS_TABLE}covered-table-cell"):
                    continue
                n = min(int(cell.get(f"{_NS_TABLE}number-columns-repeated", "1") or 1), 50)
                cells.extend([_odf_cell_text(cell)] * n)
            line = "\t".join(cells).rstrip()
            if line.strip():
                out.append(line)
                size += len(line) + 1
                if size >= max_chars:
                    return "\n".join(out)[:max_chars]
    return "\n".join(out)[:max_chars]


def _odt_text(z: zipfile.ZipFile, max_chars: int) -> str:
    root = _parse(_member(z, "content.xml"))
    if root is None:
        return ""
    blocks = ["".join(p.itertext()) for p in root.iter() if p.tag in (f"{_NS_TEXT}p", f"{_NS_TEXT}h")]
    return _tidy("\n".join(blocks))[:max_chars]


# --------------------------------------------------------------------------- plain text, archives

def _csv_text(data: bytes, max_chars: int) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        return ""
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
    except csv.Error:
        dialect = csv.excel
    rows = []
    size = 0
    for row in csv.reader(io.StringIO(text), dialect):
        line = "\t".join(c.strip() for c in row).rstrip()
        if line.strip():
            rows.append(line)
            size += len(line) + 1
            if size >= max_chars:
                break
    return "\n".join(rows)[:max_chars]


def _zip_text(z: zipfile.ZipFile, max_chars: int, pdf_text: PdfText, depth: int) -> str:
    parts = []
    budget = max_chars
    members = [i for i in z.infolist() if not i.is_dir()][:_ZIP_MEMBERS]
    for info in members:
        if budget <= 0:
            break
        data = _member(z, info.filename)
        if not data:
            continue
        text = extract(info.filename, data, budget, pdf_text, depth + 1)
        if text:
            parts.append(f"[{info.filename.rsplit('/', 1)[-1]}]\n{text}")
            budget -= len(text)
    return "\n\n".join(parts)[:max_chars]


# --------------------------------------------------------------------------- dispatcher

_OFFICE_ZIP = {"xlsx": _xlsx_text, "xlsm": _xlsx_text, "docx": _docx_text, "ods": _ods_text, "odt": _odt_text}


def kind(name: str) -> str:
    """The reader a file name points to: pdf, xlsx, docx, ods, odt, csv, zip, or "" if none."""
    n = name.lower().strip()
    while n.endswith(".p7m"):
        n = n[:-4]
    n = n.split("?")[0]
    ext = n.rsplit(".", 1)[-1] if "." in n else ""
    if ext == "pdf":
        return "pdf"
    if ext in _OFFICE_ZIP:
        return "xlsx" if ext == "xlsm" else ext
    if ext in ("csv", "txt"):
        return "csv"
    if ext == "zip":
        return "zip"
    return ""


def readable(name: str) -> bool:
    """True for the attachments this module (or the PDF reader) can turn into text."""
    return kind(name) != ""


def extract(name: str, data: bytes, max_chars: int, pdf_text: PdfText, depth: int = 0) -> str:
    """The text of an attachment, by its name and content; "" when it cannot be read."""
    if max_chars <= 0 or not data:
        return ""
    if data.startswith(b"%PDF") or kind(name) == "pdf" or (data[:1] == b"\x30" and kind(name) == ""):
        return pdf_text(data, max_chars)
    if data[:1] == b"\x30" and name.lower().endswith(".p7m"):   # a signed office file
        from indexer.p7m import unwrap
        data = unwrap(data) or b""
        if not data:
            return ""
        if data.startswith(b"%PDF"):
            return pdf_text(data, max_chars)
    k = kind(name)
    if k == "csv":
        return _csv_text(data, max_chars)
    if k in _OFFICE_ZIP or k == "zip":
        z = _open_zip(data)
        if z is None:
            return ""
        with z:
            if k == "zip":
                return "" if depth >= 1 else _zip_text(z, max_chars, pdf_text, depth)
            try:
                return _OFFICE_ZIP[k](z, max_chars)
            except (ET.ParseError, ValueError, KeyError) as e:
                log.debug(f"{name}: {e}")
                return ""
    return ""
