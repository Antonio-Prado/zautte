"""
Lists (and, on request, removes) the accounts with no activity for a long time.

An account is inactive when its last question in usage.jsonl is older than --days, or when it
never asked anything and was created (the `created` field set by adduser; older accounts have none)
more than --days ago. Accounts without any date are listed apart and never removed.

Usage (on the server, from the project directory):
    venv/bin/python -m scripts.inactive_users --days 180            # list only
    venv/bin/python -m scripts.inactive_users --days 180 --remove   # remove them, after asking

Removing an account ends its sessions at once. Its past questions follow the retention periods
(RETENTION_*); to delete them earlier use scripts.forget.
"""

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from api.auth import _load_users, write_users
from api.datalog import USAGE_FILE, parse_ts
from api.filelock import file_lock
from api.privdrop import drop_to_service_user
from config.settings import DATA_DIR, USERS_FILE, now_local


def last_activity() -> dict[str, dt.datetime]:
    """uid → time of its latest question in usage.jsonl."""
    last: dict[str, dt.datetime] = {}
    if not USAGE_FILE.exists():
        return last
    with open(USAGE_FILE, encoding="utf-8") as f:
        for line in f:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = parse_ts(e.get("ts", "")) if isinstance(e, dict) else None
            uid = e.get("uid") if isinstance(e, dict) else None
            if t is not None and uid and (uid not in last or t > last[uid]):
                last[uid] = t
    return last


def classify(users: list[dict], days: int) -> tuple[list[dict], list[dict]]:
    """(inactive, undated): the accounts to remove, and those that cannot be judged."""
    cutoff = now_local() - dt.timedelta(days=days)
    last = last_activity()
    inactive, undated = [], []
    for u in users:
        seen = last.get(u.get("id", ""))
        if seen is not None:
            if seen < cutoff:
                inactive.append(u)
            continue
        created = None
        try:
            created = dt.datetime.fromisoformat(u["created"]).replace(tzinfo=cutoff.tzinfo)
        except (KeyError, TypeError, ValueError):
            pass
        if created is None:
            undated.append(u)
        elif created < cutoff:
            inactive.append(u)
    return inactive, undated


def main() -> None:
    drop_to_service_user(DATA_DIR)
    ap = argparse.ArgumentParser(description="Accounts with no activity for a long time")
    ap.add_argument("--days", type=int, default=180, help="days without a question (default 180)")
    ap.add_argument("--remove", action="store_true", help="remove the inactive accounts (asks first)")
    args = ap.parse_args()

    with file_lock(USERS_FILE):
        users = _load_users()
        inactive, undated = classify(users, args.days)
        print(f"{len(users)} accounts; {len(inactive)} inactive for more than {args.days} days:")
        for u in inactive:
            print(f"  {u.get('name', '?'):30} {u.get('email', '?'):35} (id={u.get('id')})")
        if undated:
            print(f"{len(undated)} accounts never asked anything and have no creation date "
                  f"(not touched):")
            for u in undated:
                print(f"  {u.get('name', '?'):30} {u.get('email', '?')}")
        if not (args.remove and inactive):
            return
        if input(f"Remove {len(inactive)} accounts? [y/N] ").strip().lower() != "y":
            print("Nothing removed.")
            return
        gone = {u["id"] for u in inactive}
        write_users([u for u in users if u.get("id") not in gone])
        print(f"Removed {len(gone)} accounts.")


if __name__ == "__main__":
    main()
