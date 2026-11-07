"""OCR of the PDFs without usable text (indexer/pdf_extractor.py): when it runs, what it keeps."""
import subprocess
from pathlib import Path

from indexer import pdf_extractor as pe


def test_legible():
    assert pe.legible("Il presente atto disciplina l'utilizzo della sala consiliare e degli spazi adiacenti.")
    assert pe.legible("Bilancio 2026: entrate 44.048.515,35 €; spese 41.000.000,00 € (titolo I), residui 21,69 mln")
    assert not pe.legible("")
    assert not pe.legible("2 \n 3 \n 4")                                   # page numbers only
    assert not pe.legible("\x01\x02\x03\x04\x05 abc \x16\x08\x06\x18" * 10)   # PDF24 without a Unicode map
    assert not pe.legible("□□□□ ▯▯▯ ◊◊◊ the only words here " * 3)          # mostly symbols
    garbage = "!!4!\n) 8,-1\n$: 8\n’ 8(\n)/ !2\n’/ !2!\n/ ,; +8!\n! 8!+\n(+(2\n8-6#,% + (\n8!+ (\n1!!/+\n( 8 +\n+. ( 8\n"
    assert not pe.legible(garbage * 6 + "ASSCOLI PICENO " * 2)           # digits and punctuation, few letters


def test_ocr_available_follows_the_setting(monkeypatch):
    monkeypatch.setattr(pe.shutil, "which", lambda name: "/usr/local/bin/" + name)
    monkeypatch.setattr(pe, "PDF_OCR", "auto")
    assert pe.ocr_available()
    monkeypatch.setattr(pe, "PDF_OCR", "off")
    assert not pe.ocr_available()
    monkeypatch.setattr(pe, "PDF_OCR", "on")
    monkeypatch.setattr(pe.shutil, "which", lambda name: None)
    assert not pe.ocr_available()


def _fake_run(pages_text):
    """A subprocess.run that writes the page images for pdftoppm and answers tesseract with the text."""
    def run(cmd, check, capture_output, timeout):
        if cmd[0] == "pdftoppm":
            prefix = Path(cmd[-1])
            for n in range(1, len(pages_text) + 1):
                (prefix.parent / f"pg-{n:02d}.png").write_bytes(b"png")
            return subprocess.CompletedProcess(cmd, 0, b"", b"")
        page = int(Path(cmd[1]).stem.split("-")[1])
        return subprocess.CompletedProcess(cmd, 0, pages_text[page - 1].encode(), b"")
    return run


def test_ocr_text_pages(monkeypatch, tmp_path):
    monkeypatch.setattr(pe.subprocess, "run", _fake_run(["DISCIPLINARE PER L'UTILIZZO DELLA SALA", "", "Art. 3 Per la sua finalità"]))
    text = pe.ocr_text(tmp_path / "sala.pdf", max_pages=20)
    assert text == "[Pagina 1]\nDISCIPLINARE PER L'UTILIZZO DELLA SALA\n\n[Pagina 3]\nArt. 3 Per la sua finalità"


def test_ocr_text_failures(monkeypatch, tmp_path):
    def boom(cmd, check, capture_output, timeout):
        raise subprocess.CalledProcessError(1, cmd, stderr=b"no such file")
    monkeypatch.setattr(pe.subprocess, "run", boom)
    assert pe.ocr_text(tmp_path / "x.pdf") == ""

    def slow(cmd, check, capture_output, timeout):
        raise subprocess.TimeoutExpired(cmd, timeout)
    monkeypatch.setattr(pe.subprocess, "run", slow)
    assert pe.ocr_text(tmp_path / "x.pdf") == ""


def test_with_ocr_replaces_only_illegible_text(monkeypatch, tmp_path):
    monkeypatch.setattr(pe, "ocr_available", lambda: True)
    monkeypatch.setattr(pe, "ocr_text", lambda path: "[Pagina 1]\nTesto riconosciuto dalle immagini della scansione.")
    good = "Un testo normale letto da pypdf, con abbastanza lettere per essere considerato leggibile."
    assert pe._with_ocr(tmp_path / "a.pdf", good) == good
    assert pe._with_ocr(tmp_path / "b.pdf", "") == "[Pagina 1]\nTesto riconosciuto dalle immagini della scansione."
    monkeypatch.setattr(pe, "ocr_text", lambda path: "□□□ □□")      # the OCR found nothing better
    assert pe._with_ocr(tmp_path / "c.pdf", "2 \n 3") == "2 \n 3"
    monkeypatch.setattr(pe, "ocr_available", lambda: False)
    assert pe._with_ocr(tmp_path / "d.pdf", "") == ""
