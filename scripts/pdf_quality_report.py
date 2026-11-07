"""
The PDFs published without searchable text, to have them published again as PDF/A.

A document a public body publishes must be in an open, accessible format: a PDF with searchable
text (PDF/A, level «u» or «a»). A scan, or a file whose fonts have no Unicode map, is not, even
when Zautte reads it with the OCR. The readers log those files in PDF_QUALITY_FILE
(indexer/pdf_extractor.py, record_pdf_quality); this script mails the list of the last `--days`
days to PDF_QUALITY_REPORT_TO, grouped by site and by document, so the offices that publish them
can be told. Nothing is sent when the list is empty.

Run (weekly from cron, as root: the SMTP settings are in .env):
    venv/bin/python -m scripts.pdf_quality_report [--days 7] [--dry-run]
Exit codes: 0 sent or nothing to send, 1 no address or send error.
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import (
    PDF_QUALITY_FILE,
    PDF_QUALITY_REPORT_TO,
    SITE_NAME,
    now_local,
)

log = logging.getLogger("pdf_quality_report")

_LABEL = {"ocr": "solo immagine: letto con il riconoscimento ottico dei caratteri",
          "none": "senza testo leggibile, nemmeno con il riconoscimento ottico"}


def read_entries(path: Path, since: datetime) -> list[dict]:
    """The log lines since `since`, the latest per (url, attachment)."""
    latest: dict[tuple[str, str], dict] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    for line in lines:
        try:
            e = json.loads(line)
            ts = datetime.fromisoformat(e["ts"])
        except (ValueError, KeyError, TypeError):
            continue
        if ts < since or e.get("method") not in _LABEL:
            continue
        latest[(e.get("url", ""), e.get("attachment", ""))] = e
    return sorted(latest.values(), key=lambda e: (urlsplit(e.get("url", "")).netloc, e.get("document", ""), e.get("attachment", "")))


def build_report(entries: list[dict], days: int, site: str, today: datetime) -> tuple[str, str]:
    """(subject, body) of the mail: the files by host, each with how it was read."""
    by_host: dict[str, list[dict]] = {}
    for e in entries:
        by_host.setdefault(urlsplit(e.get("url", "")).netloc or "?", []).append(e)
    lines = [(f"PDF pubblicati senza testo ricercabile, letti dai job di Zautte negli ultimi {days} giorni "
              f"(al {today:%d/%m/%Y}): {len(entries)} file."), "",
             ("Un documento pubblicato deve essere in formato aperto e accessibile: PDF/A con testo ricercabile "
              "(livello «u» o «a»), prodotto dall'originale e non da una scansione né con PDF24. Zautte li legge "
              "con il riconoscimento ottico, ma il file resta non conforme finché non viene ripubblicato."), ""]
    for host, items in by_host.items():
        lines.append(f"== {host} ({len(items)})")
        for e in items:
            what = f"«{e['document']}» — allegato {e['attachment']}" if e.get("attachment") else e.get("url", "")
            lines.append(f"- {what}")
            lines.append(f"  {_LABEL[e['method']]}; pagina: {e.get('url', '')}")
        lines.append("")
    lines.append(f"Elenco automatico di Zautte ({site}); i file sono quelli letti o riletti nel periodo, "
                 "gli altri non conformi compaiono quando vengono riletti.")
    return f"Zautte: {len(entries)} PDF da ripubblicare in PDF/A (ultimi {days} giorni)", "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="PDFs without searchable text → weekly mail")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--dry-run", action="store_true", help="print the mail, send nothing")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    today = now_local()
    entries = read_entries(PDF_QUALITY_FILE, today - timedelta(days=args.days))
    if not entries:
        log.info("no PDF without searchable text in the last %d days: nothing sent", args.days)
        return 0
    subject, body = build_report(entries, args.days, SITE_NAME, today)
    if args.dry_run:
        print(subject, "\n\n", body)
        return 0
    if not PDF_QUALITY_REPORT_TO:
        log.error("PDF_QUALITY_REPORT_TO (or FEEDBACK_NOTIFY_EMAIL) is not set: %d files not reported", len(entries))
        return 1
    from api.mailer import send_email
    try:
        send_email(PDF_QUALITY_REPORT_TO, subject, body)
    except Exception as exc:  # noqa: BLE001 - any SMTP error: say it and stop
        log.error("report not sent: %s", exc)
        return 1
    log.info("report sent to %s: %d files", PDF_QUALITY_REPORT_TO, len(entries))
    return 0


if __name__ == "__main__":
    sys.exit(main())
