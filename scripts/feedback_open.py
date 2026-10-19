"""
Lists the negative feedback with the details reported by colleagues (comment and
links to the pages with the correct information), so whoever works on the knowledge
base — the administrator or an assistant — has everything in one go.

Usage (on the server, from the project directory):
    venv/bin/python -m scripts.feedback_open              # open ones, most recent first
    venv/bin/python -m scripts.feedback_open --details    # only with comment or link
    venv/bin/python -m scripts.feedback_open --all        # also those already resolved
    venv/bin/python -m scripts.feedback_open --json       # JSON (for scripts)
    venv/bin/python -m scripts.feedback_open --limit 20

To mark a feedback as resolved: "✓ Risolto" button in the dashboard
(administrator view) or POST /feedback/resolve with {ts, question}.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from api import feedback_store as fs


def format_entry(e: dict) -> str:
    ts = (e.get("ts") or "").replace("T", " ")[:16]
    fid = e.get("id") or "—"
    head = f"[{fid}] {ts}"
    if e.get("user"):
        head += f"  👤 {e['user']}"
    if e.get("resolved"):
        head += "  ✓ risolto"
    lines = [head, f"  D: {e.get('question', '')}"]
    if e.get("answer_preview"):
        lines.append(f"  R: {e['answer_preview'].replace(chr(10), ' ')}")
    if e.get("comment"):
        lines.append(f"  💬 {e['comment']}")
    for u in e.get("urls") or []:
        lines.append(f"  🔗 {u}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Feedback negativi con dettagli")
    ap.add_argument("--all", action="store_true", help="includi anche i feedback risolti")
    ap.add_argument("--details", action="store_true", help="solo feedback con commento o link")
    ap.add_argument("--json", action="store_true", help="output JSON")
    ap.add_argument("--limit", type=int, default=200)
    args = ap.parse_args(argv)

    items, open_count, total = fs.negative_open(fs.load_resolved(), limit=args.limit,
                                                include_resolved=args.all)
    items = list(reversed(items))  # most recent first
    if args.details:
        items = [e for e in items if e.get("comment") or e.get("urls")]

    if args.json:
        print(json.dumps({"items": items, "open": open_count, "total": total},
                         ensure_ascii=False, indent=2))
        return 0

    print(f"Feedback negativi aperti: {open_count} (feedback totali: {total}); mostrati: {len(items)}\n")
    for e in items:
        print(format_entry(e))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
