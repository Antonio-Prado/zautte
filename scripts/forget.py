"""
Delete on request a user's questions, or individual questions, from all the
data files and from the memory of the running API, without restarting it.

Usage (on the server, from the project directory; ADMIN_API_KEY is needed in .env):
    venv/bin/python -m scripts.forget --user m.rossi@example.it --last 1
    venv/bin/python -m scripts.forget --user <email o id> --last 5
    venv/bin/python -m scripts.forget --user <email o id> --all   (also ids of removed users)
    venv/bin/python -m scripts.forget --rid 3f9a0c1d2e4b [--rid ...]
      --dry-run   show what would be deleted and stop
      --yes       do not ask for confirmation

It goes through POST /usage/forget of the running API: it shows the questions found
and, after confirmation, removes them from the files and from the process memory. The rid of a question is read in the API log (/var/log/chatbot.log), which
does not contain the text, and in usage.jsonl, answers.jsonl, gaps.jsonl and feedback.jsonl.

Left out: the notification email of a report already sent and, for the
questions that reached the model, the copy kept by the provider (Anthropic: 30
days with the direct API).
"""

import argparse
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import ADMIN_API_KEY, USERS_FILE

DEFAULT_API = "http://127.0.0.1:8000"


def _find_user(user: str) -> tuple[str, str]:
    """(id, name) of the user given by email or by id."""
    try:
        users = json.loads(USERS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        users = []
    key = user.strip().lower()
    for u in users if isinstance(users, list) else []:
        if key in ((u.get("email") or "").strip().lower(), u.get("id")):
            return u["id"], u.get("name", "")
    if "@" in key:
        sys.exit(f"User not found in {USERS_FILE}: {user}")
    # Id of an already removed user (adduser --remove): their questions remain in usage.jsonl
    print(f"Id {user} not present in {USERS_FILE}: using it as is")
    return user.strip(), ""


def _forget(client: httpx.Client, base: str, body: dict) -> dict:
    try:
        r = client.post(f"{base}/usage/forget", json=body, headers={"X-Admin-Key": ADMIN_API_KEY})
    except httpx.HTTPError as e:
        sys.exit(f"API unreachable at {base}: {e}")
    if r.status_code != 200:
        sys.exit(f"{base}: error {r.status_code} {r.text[:200]}")
    return r.json()


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete questions from the logs on request")
    parser.add_argument("--user", help="the user's email or id")
    who = parser.add_mutually_exclusive_group()
    who.add_argument("--last", type=int, default=0, help="the user's last N questions")
    who.add_argument("--all", action="store_true", help="all of the user's questions and feedback")
    parser.add_argument("--rid", action="append", default=[], help="identifier of a question")
    parser.add_argument("--api", default=DEFAULT_API, help=f"API address (default: {DEFAULT_API})")
    parser.add_argument("--dry-run", action="store_true", help="show without deleting")
    parser.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    args = parser.parse_args()

    selection: dict = {}
    if args.rid:
        selection["rids"] = args.rid
    if args.user:
        uid, name = _find_user(args.user)
        selection["uid"] = uid
        if args.all:
            selection["all"] = True
        else:
            selection["last"] = args.last or 1
        print(f"User: {name or uid} ({uid})")
    elif args.last or args.all:
        parser.error("--last and --all require --user")
    if not selection:
        parser.error("specify --user or --rid")

    with httpx.Client(timeout=30) as client:
        preview = _forget(client, args.api, {**selection, "dry_run": True})
        questions = preview["questions"]
        if not questions and not preview["feedback"]:
            print("No questions found.")
            return
        print(f"Questions found: {len(questions)}")
        for q in questions:
            text = (q.get("q") or "(testo già scaduto)").replace("\n", " ")
            print(f"  {q.get('ts', '')[:19].replace('T', ' ')}  [{q.get('rid') or 'no rid'}]  {text[:100]}")
        print(f"To remove: {preview['usage']} usage entries, {preview.get('answers', 0)} answers, "
              f"{preview['gaps']} lacune, {preview['feedback']} feedback, "
              f"{preview['resolved']} marcature «risolto»")
        if args.dry_run:
            return
        if not args.yes and input("Delete? [y/N] ").strip().lower() not in ("s", "si", "sì", "y", "yes"):
            print("Cancelled.")
            return

        # From here on the choice is fixed to what was shown (except --all):
        # a question that arrived in the meantime is not taken in place of another.
        if selection.get("all"):
            fixed = selection
        else:
            fixed = {"rids": sorted(set(args.rid) | {q["rid"] for q in questions if q.get("rid")})}
            legacy_ts = [q["ts"] for q in questions if not q.get("rid")]
            if legacy_ts:
                fixed.update(uid=selection["uid"], ts=legacy_ts)

        r = _forget(client, args.api, fixed)
        print(f"Removed {r['usage']} usage entries, {r.get('answers', 0)} answers, {r['gaps']} gaps, "
              f"{r['feedback']} feedback, {r['resolved']} 'resolved' marks, "
              f"{r['memory']} from memory")


if __name__ == "__main__":
    main()
