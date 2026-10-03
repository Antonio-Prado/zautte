"""
Applica i tempi di conservazione ai log con dati degli utenti (data/).

  usage.jsonl     → dopo RETENTION_USAGE_TEXT_DAYS toglie il testo della domanda,
                    dopo RETENTION_USAGE_DAYS elimina la voce
  gaps.jsonl      → elimina le voci più vecchie di RETENTION_GAPS_DAYS
  feedback.jsonl  → elimina le voci più vecchie di RETENTION_FEEDBACK_DAYS
                    (e le relative marcature "risolto" in resolved_negative.json)

Un valore 0 disattiva la scadenza corrispondente. Da lanciare ogni giorno via
cron, con lo stesso utente del servizio oppure come root (i file riscritti
mantengono proprietario e permessi):

    python -m scripts.purge_logs            # applica
    python -m scripts.purge_logs --dry-run  # mostra solo cosa farebbe

Stampa solo conteggi, mai il contenuto delle voci.
"""

import argparse
import datetime as dt
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import (
    DATA_DIR,
    RETENTION_FEEDBACK_DAYS,
    RETENTION_GAPS_DAYS,
    RETENTION_USAGE_DAYS,
    RETENTION_USAGE_TEXT_DAYS,
    now_local,
)

USAGE_FILE = DATA_DIR / "usage.jsonl"
GAPS_FILE = DATA_DIR / "gaps.jsonl"
FEEDBACK_FILE = DATA_DIR / "feedback.jsonl"
RESOLVED_FILE = DATA_DIR / "resolved_negative.json"


def _parse_ts(ts: str) -> dt.datetime | None:
    try:
        t = dt.datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.astimezone()   # ora locale se senza fuso


def _cutoff(days: int) -> dt.datetime | None:
    return now_local() - dt.timedelta(days=days) if days > 0 else None


def _older(ts: str, cutoff: dt.datetime | None) -> bool:
    if cutoff is None:
        return False
    t = _parse_ts(ts)
    return t is not None and t < cutoff


def _replace_keeping_owner(path: Path, tmp: Path) -> None:
    """Sostituisce `path` con `tmp` mantenendo proprietario e permessi."""
    st = path.stat()
    os.chmod(tmp, st.st_mode & 0o7777)
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        os.chown(tmp, st.st_uid, st.st_gid)
    tmp.replace(path)


def _rewrite_jsonl(path: Path, transform: Callable[[dict], dict | None],
                   dry_run: bool) -> tuple[int, int]:
    """Riscrive un file JSONL applicando `transform` a ogni voce (None = elimina).
    Ritorna (voci eliminate, voci modificate). Le righe non JSON restano.

    L'API continua ad aggiungere righe mentre il file viene riscritto: prima
    della sostituzione si accodano le righe arrivate nel frattempo, così la
    finestra in cui una scrittura può andare persa si riduce a pochi istanti."""
    if not path.exists():
        return 0, 0
    with open(path, encoding="utf-8") as f:
        content = f.read()
        size = f.tell()
    removed = changed = 0
    out: list[str] = []
    for line in content.splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            out.append(line)
            continue
        new = transform(dict(entry))
        if new is None:
            removed += 1
            continue
        if new != entry:
            changed += 1
            line = json.dumps(new, ensure_ascii=False)
        out.append(line)
    if dry_run or not (removed or changed):
        return removed, changed
    tmp = path.with_name(path.name + ".purge.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        if out:
            f.write("\n".join(out) + "\n")
        with open(path, encoding="utf-8") as src:
            src.seek(size)
            f.write(src.read())   # righe aggiunte durante la riscrittura
    _replace_keeping_owner(path, tmp)
    return removed, changed


def purge_usage(dry_run: bool) -> tuple[int, int]:
    drop_cut = _cutoff(RETENTION_USAGE_DAYS)
    text_cut = _cutoff(RETENTION_USAGE_TEXT_DAYS)

    def transform(e: dict) -> dict | None:
        ts = e.get("ts", "")
        if _older(ts, drop_cut):
            return None
        if "q" in e and _older(ts, text_cut):
            del e["q"]
        return e

    return _rewrite_jsonl(USAGE_FILE, transform, dry_run)


def purge_gaps(dry_run: bool) -> int:
    cut = _cutoff(RETENTION_GAPS_DAYS)
    removed, _ = _rewrite_jsonl(GAPS_FILE, lambda e: None if _older(e.get("ts", ""), cut) else e, dry_run)
    return removed


def purge_feedback(dry_run: bool) -> tuple[int, int]:
    cut = _cutoff(RETENTION_FEEDBACK_DAYS)
    removed, _ = _rewrite_jsonl(FEEDBACK_FILE, lambda e: None if _older(e.get("ts", ""), cut) else e, dry_run)

    # Marcature "risolto": chiave "<ts del feedback>::<domanda>"
    resolved_removed = 0
    if cut is not None and RESOLVED_FILE.exists():
        try:
            items = json.loads(RESOLVED_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            items = None
        if isinstance(items, list):
            keep = [i for i in items
                    if not (isinstance(i, dict) and _older(str(i.get("key", "")).split("::", 1)[0], cut))]
            resolved_removed = len(items) - len(keep)
            if resolved_removed and not dry_run:
                tmp = RESOLVED_FILE.with_name(RESOLVED_FILE.name + ".purge.tmp")
                tmp.write_text(json.dumps(keep, ensure_ascii=False, indent=2), encoding="utf-8")
                _replace_keeping_owner(RESOLVED_FILE, tmp)
    return removed, resolved_removed


def main() -> None:
    parser = argparse.ArgumentParser(description="Applica i tempi di conservazione ai log degli utenti")
    parser.add_argument("--dry-run", action="store_true", help="mostra i conteggi senza modificare i file")
    args = parser.parse_args()

    u_removed, u_stripped = purge_usage(args.dry_run)
    g_removed = purge_gaps(args.dry_run)
    f_removed, r_removed = purge_feedback(args.dry_run)

    prefix = "[dry-run] " if args.dry_run else ""
    print(f"{prefix}usage.jsonl: {u_removed} voci eliminate, testo tolto da {u_stripped} "
          f"(scadenze: testo {RETENTION_USAGE_TEXT_DAYS} gg, voce {RETENTION_USAGE_DAYS} gg)")
    print(f"{prefix}gaps.jsonl: {g_removed} voci eliminate (scadenza {RETENTION_GAPS_DAYS} gg)")
    print(f"{prefix}feedback.jsonl: {f_removed} voci eliminate, {r_removed} marcature 'risolto' tolte "
          f"(scadenza {RETENTION_FEEDBACK_DAYS} gg)")


if __name__ == "__main__":
    main()
