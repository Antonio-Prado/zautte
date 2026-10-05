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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from api.datalog import (
    FEEDBACK_FILE,
    GAPS_FILE,
    USAGE_FILE,
    drop_resolved,
    parse_ts,
    rewrite_jsonl,
)
from config.settings import (
    RETENTION_FEEDBACK_DAYS,
    RETENTION_GAPS_DAYS,
    RETENTION_USAGE_DAYS,
    RETENTION_USAGE_TEXT_DAYS,
    now_local,
)


def _cutoff(days: int) -> dt.datetime | None:
    return now_local() - dt.timedelta(days=days) if days > 0 else None


def _older(ts: str, cutoff: dt.datetime | None) -> bool:
    if cutoff is None:
        return False
    t = parse_ts(ts)
    return t is not None and t < cutoff


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

    return rewrite_jsonl(USAGE_FILE, transform, dry_run)


def purge_gaps(dry_run: bool) -> int:
    cut = _cutoff(RETENTION_GAPS_DAYS)
    removed, _ = rewrite_jsonl(GAPS_FILE, lambda e: None if _older(e.get("ts", ""), cut) else e, dry_run)
    return removed


def purge_feedback(dry_run: bool) -> tuple[int, int]:
    cut = _cutoff(RETENTION_FEEDBACK_DAYS)
    removed, _ = rewrite_jsonl(FEEDBACK_FILE, lambda e: None if _older(e.get("ts", ""), cut) else e, dry_run)
    # Marcature "risolto": chiave "<ts del feedback>::<domanda>"
    resolved_removed = (drop_resolved(lambda key: _older(key.split("::", 1)[0], cut), dry_run)
                        if cut is not None else 0)
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
