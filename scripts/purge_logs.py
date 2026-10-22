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
cron. Started as root it first switches to the owner of the data folder
(api/privdrop.py), so root never writes where the service account controls:

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
    rename_resolved,
    rewrite_jsonl,
)
from api.feedback_store import resolved_key
from api.privdrop import drop_to_service_user
from config.settings import (
    DATA_DIR,
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
    """A gap is a question text: it never outlives RETENTION_USAGE_TEXT_DAYS."""
    cut = _cutoff(min(d for d in (RETENTION_GAPS_DAYS, RETENTION_USAGE_TEXT_DAYS) if d > 0) if
                  (RETENTION_GAPS_DAYS > 0 or RETENTION_USAGE_TEXT_DAYS > 0) else 0)
    removed, _ = rewrite_jsonl(GAPS_FILE, lambda e: None if _older(e.get("ts", ""), cut) else e, dry_run)
    return removed


_FEEDBACK_TEXT = ("question", "answer_preview", "comment", "urls", "user")


def purge_feedback(dry_run: bool) -> tuple[int, int, int]:
    """Old feedback is deleted; before that, past RETENTION_USAGE_TEXT_DAYS, it loses its texts
    (question, answer excerpt, comment, links, author's name) and keeps the vote, the dates and
    the ids, so the counters of the panel do not change. Returns (deleted, stripped, marks removed)."""
    cut = _cutoff(RETENTION_FEEDBACK_DAYS)
    text_cut = _cutoff(RETENTION_USAGE_TEXT_DAYS)
    renamed: dict[str, str] = {}   # "resolved" marks of rows without id are keyed by time and question

    def transform(e: dict) -> dict | None:
        ts = e.get("ts", "")
        if _older(ts, cut):
            return None
        if _older(ts, text_cut) and any(k in e for k in _FEEDBACK_TEXT):
            if not e.get("id") and e.get("question"):
                renamed[resolved_key(ts, e["question"])] = resolved_key(ts, "")
            return {k: v for k, v in e.items() if k not in _FEEDBACK_TEXT}
        return e

    removed, stripped = rewrite_jsonl(FEEDBACK_FILE, transform, dry_run)
    if renamed:
        rename_resolved(renamed, dry_run)
    # "risolto" marks: `ts` of the feedback, or (older marks) the start of the key "<ts>::<question>"
    resolved_removed = (drop_resolved(
        lambda i: _older(str(i.get("ts") or str(i.get("key", "")).split("::", 1)[0]), cut), dry_run)
                        if cut is not None else 0)
    return removed, stripped, resolved_removed


def purge_revoked(dry_run: bool) -> int:
    """Expired entries of the logout revocation list."""
    from api.auth import _REVOKED_FILE, _revoked_keys, prune_revoked
    if dry_run:
        now = int(dt.datetime.now(dt.UTC).timestamp())
        return sum(1 for v in _revoked_keys().values() if v <= now) if _REVOKED_FILE.exists() else 0
    return prune_revoked()


def main() -> None:
    drop_to_service_user(DATA_DIR)
    parser = argparse.ArgumentParser(description="Apply the retention periods to the user logs")
    parser.add_argument("--dry-run", action="store_true", help="show the counts without modifying the files")
    args = parser.parse_args()

    u_removed, u_stripped = purge_usage(args.dry_run)
    a_removed = purge_answers(args.dry_run)
    g_removed = purge_gaps(args.dry_run)
    f_removed, f_stripped, r_removed = purge_feedback(args.dry_run)

    revoked = purge_revoked(args.dry_run)

    prefix = "[dry-run] " if args.dry_run else ""
    print(f"{prefix}usage.jsonl: {u_removed} entries deleted, text removed from {u_stripped} "
          f"(retention: text {RETENTION_USAGE_TEXT_DAYS} d, entry {RETENTION_USAGE_DAYS} d)")
    print(f"{prefix}answers.jsonl: {a_removed} answers deleted (retention {RETENTION_USAGE_TEXT_DAYS} d)")
    print(f"{prefix}gaps.jsonl: {g_removed} entries deleted (retention {RETENTION_GAPS_DAYS} d, "
          f"never beyond the question text, {RETENTION_USAGE_TEXT_DAYS} d)")
    print(f"{prefix}feedback.jsonl: {f_removed} entries deleted, texts removed from {f_stripped} "
          f"(after {RETENTION_USAGE_TEXT_DAYS} d), {r_removed} 'resolved' marks removed "
          f"(retention {RETENTION_FEEDBACK_DAYS} d)")
    print(f"{prefix}revoked_tokens.json: {revoked} expired entries dropped")


if __name__ == "__main__":
    main()
