"""
Applies the retention periods to the logs containing user data (data/).

  usage.jsonl     → after RETENTION_USAGE_TEXT_DAYS removes the question text,
                    after RETENTION_USAGE_DAYS deletes the entry
  answers.jsonl   → deletes answers older than RETENTION_USAGE_TEXT_DAYS
                    (same expiry as the question text)
  gaps.jsonl      → deletes entries older than RETENTION_GAPS_DAYS
  feedback.jsonl  → deletes entries older than RETENTION_FEEDBACK_DAYS
                    (and the related "risolto" marks in resolved_negative.json)

A value of 0 disables the corresponding expiry. To be run every day via
cron, with the same user as the service or as root (rewritten files
keep owner and permissions):

    python -m scripts.purge_logs            # apply
    python -m scripts.purge_logs --dry-run  # only show what it would do

Prints only counts, never the content of the entries.
"""

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from api.datalog import (
    ANSWERS_FILE,
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


def purge_answers(dry_run: bool) -> int:
    cut = _cutoff(RETENTION_USAGE_TEXT_DAYS)
    removed, _ = rewrite_jsonl(ANSWERS_FILE, lambda e: None if _older(e.get("ts", ""), cut) else e,
                               dry_run)
    return removed


def purge_gaps(dry_run: bool) -> int:
    cut = _cutoff(RETENTION_GAPS_DAYS)
    removed, _ = rewrite_jsonl(GAPS_FILE, lambda e: None if _older(e.get("ts", ""), cut) else e, dry_run)
    return removed


def purge_feedback(dry_run: bool) -> tuple[int, int]:
    cut = _cutoff(RETENTION_FEEDBACK_DAYS)
    removed, _ = rewrite_jsonl(FEEDBACK_FILE, lambda e: None if _older(e.get("ts", ""), cut) else e, dry_run)
    # "risolto" marks: key "<ts del feedback>::<domanda>"
    resolved_removed = (drop_resolved(lambda key: _older(key.split("::", 1)[0], cut), dry_run)
                        if cut is not None else 0)
    return removed, resolved_removed


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply the retention periods to the user logs")
    parser.add_argument("--dry-run", action="store_true", help="show the counts without modifying the files")
    args = parser.parse_args()

    u_removed, u_stripped = purge_usage(args.dry_run)
    a_removed = purge_answers(args.dry_run)
    g_removed = purge_gaps(args.dry_run)
    f_removed, r_removed = purge_feedback(args.dry_run)

    prefix = "[dry-run] " if args.dry_run else ""
    print(f"{prefix}usage.jsonl: {u_removed} entries deleted, text removed from {u_stripped} "
          f"(retention: text {RETENTION_USAGE_TEXT_DAYS} d, entry {RETENTION_USAGE_DAYS} d)")
    print(f"{prefix}answers.jsonl: {a_removed} answers deleted (retention {RETENTION_USAGE_TEXT_DAYS} d)")
    print(f"{prefix}gaps.jsonl: {g_removed} entries deleted (retention {RETENTION_GAPS_DAYS} d)")
    print(f"{prefix}feedback.jsonl: {f_removed} entries deleted, {r_removed} 'resolved' marks removed "
          f"(retention {RETENTION_FEEDBACK_DAYS} d)")


if __name__ == "__main__":
    main()
