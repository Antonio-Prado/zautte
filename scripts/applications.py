"""
Applications of the citizens who asked to take part in the pilot (POST /apply).

Usage (on the server, from the project directory):
    venv/bin/python -m scripts.applications                 # the applications still to decide
    venv/bin/python -m scripts.applications --all           # every application, with its status
    venv/bin/python -m scripts.applications --accept <id>   # create the account and send the invitation
    venv/bin/python -m scripts.applications --reject <id>   # mark it as not selected (nothing is sent)

Accepting creates the account like `adduser --send-email`: the person receives a link to choose
the password, and makes the participation declaration at the first access.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from api.privdrop import drop_to_service_user
from config.settings import DATA_DIR


def main() -> None:
    drop_to_service_user(DATA_DIR)
    from api import applications as apps
    from api.mailer import send_invitation, smtp_configured

    ap = argparse.ArgumentParser(description="Applications to the pilot")
    ap.add_argument("--all", action="store_true", help="also the accepted and rejected ones")
    ap.add_argument("--accept", metavar="ID", help="create the account and send the invitation")
    ap.add_argument("--reject", metavar="ID", help="mark as not selected")
    args = ap.parse_args()

    if args.accept:
        if not smtp_configured():
            print("SMTP not configured: the invitation cannot be sent. Nothing done.")
            sys.exit(1)
        try:
            user, link = apps.accept(args.accept)
        except ValueError as e:
            print(f"Not accepted: {e}")
            sys.exit(1)
        try:
            send_invitation(user["email"], user["name"], link, apps.INVITE_HOURS)
        except Exception as e:  # noqa: BLE001 - the account exists: say what to do next
            print(f"Account created but the email FAILED ({e}): send the invitation again with "
                  f"`adduser --email {user['email']} --name '{user['name']}' --send-email`")
            sys.exit(1)
        apps.mark(args.accept, "accepted")
        print(f"Accepted: {user['name']} <{user['email']}>; invitation sent (link valid {apps.INVITE_HOURS} h)")
        return

    if args.reject:
        if apps.mark(args.reject, "rejected") is None:
            print("Application not found")
            sys.exit(1)
        print("Marked as not selected")
        return

    rows = apps.list_all()
    if not args.all:
        rows = [r for r in rows if r.get("status", "new") == "new"]
    if not rows:
        print("No applications" + ("" if args.all else " to decide"))
        return
    for r in rows:
        print(f"{r.get('id')}  {r.get('ts', '')[:16]}  {r.get('status', 'new'):9} {r.get('name', ''):30} "
              f"{r.get('email', ''):32} {r.get('age', '')!s:>3}  {r.get('district', '')}")
        if r.get("usage"):
            print(f"    {r['usage']}")


if __name__ == "__main__":
    main()
