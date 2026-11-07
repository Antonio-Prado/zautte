"""The PDFs without searchable text: logged by the readers (record_pdf_quality) and reported weekly
(scripts/pdf_quality_report.py)."""
import json
from datetime import datetime

from indexer import pdf_extractor as pe
from scripts import pdf_quality_report as rep


def test_record_only_when_the_text_was_not_its_own(monkeypatch, tmp_path):
    path = tmp_path / "pdf_quality.jsonl"
    monkeypatch.setattr(pe, "PDF_QUALITY_FILE", path)
    monkeypatch.setattr(pe, "last_method", "pypdf")
    pe.record_pdf_quality("https://example.org/a.pdf", 1000)
    assert not path.exists()
    monkeypatch.setattr(pe, "last_method", "ocr")
    pe.record_pdf_quality("https://example.org/a.pdf", 5154)
    monkeypatch.setattr(pe, "last_method", "none")
    pe.record_pdf_quality("https://portal.example.org/?voceamt=545&dettaglio=1", 0, document="Procedura", attachment="Tavola 3.pdf")
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
    assert [r["method"] for r in rows] == ["ocr", "none"]
    assert rows[1]["document"] == "Procedura" and rows[1]["attachment"] == "Tavola 3.pdf" and rows[0]["chars"] == 5154


def test_with_ocr_sets_the_method(monkeypatch, tmp_path):
    monkeypatch.setattr(pe, "ocr_available", lambda: True)
    monkeypatch.setattr(pe, "ocr_text", lambda path: "Testo riconosciuto dalle immagini della scansione, abbastanza lungo.")
    monkeypatch.setattr(pe, "last_method", "pypdf")
    pe._with_ocr(tmp_path / "a.pdf", "Un testo normale letto da pypdf, con abbastanza lettere per essere leggibile.")
    assert pe.last_method == "pypdf"
    pe._with_ocr(tmp_path / "b.pdf", "")
    assert pe.last_method == "ocr"
    monkeypatch.setattr(pe, "ocr_text", lambda path: "")
    pe._with_ocr(tmp_path / "c.pdf", "")
    assert pe.last_method == "none"


def test_report_groups_by_host_and_keeps_the_latest(tmp_path):
    path = tmp_path / "pdf_quality.jsonl"
    rows = [
        {"ts": "2026-10-20T10:00:00+02:00", "url": "https://files.example.org/vecchio.pdf", "document": "", "attachment": "", "method": "none", "chars": 0},
        {"ts": "2026-11-07T12:00:00+01:00", "url": "https://files.example.org/sala.pdf", "document": "", "attachment": "", "method": "none", "chars": 0},
        {"ts": "2026-11-07T13:00:00+01:00", "url": "https://files.example.org/sala.pdf", "document": "", "attachment": "", "method": "ocr", "chars": 5154},
        {"ts": "2026-11-07T13:30:00+01:00", "url": "https://portal.example.org/?voceamt=545&dettaglio=1", "document": "Procedura negoziata", "attachment": "Tavola 3.pdf", "method": "ocr", "chars": 41251},
        {"ts": "2026-11-07T13:31:00+01:00", "url": "https://portal.example.org/?voceamt=545&dettaglio=1", "document": "Procedura negoziata", "attachment": "Relazione.pdf", "method": "pypdf", "chars": 9000},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    today = datetime.fromisoformat("2026-11-07T14:00:00+01:00")
    entries = rep.read_entries(path, today - rep.timedelta(days=7))
    assert [(e["url"][-8:], e["method"]) for e in entries] == [("sala.pdf", "ocr"), ("taglio=1", "ocr")]
    subject, body = rep.build_report(entries, 7, "Comune di Esempio", today)
    assert subject == "Zautte: 2 PDF da ripubblicare in PDF/A (ultimi 7 giorni)"
    assert "== files.example.org (1)" in body and "== portal.example.org (1)" in body
    assert "«Procedura negoziata» — allegato Tavola 3.pdf" in body
    assert "solo immagine: letto con il riconoscimento ottico" in body and "PDF/A" in body
    assert rep.read_entries(tmp_path / "missing.jsonl", today) == []
