"""
Zautte user provisioning (pilot project login).

Usage:
    # create/update a user (password asked interactively, hidden)
    python -m scripts.adduser --email m.rossi@example.it --name "Mario Rossi"

    # create the user and email them a single-use link to choose their own password
    python -m scripts.adduser --email m.rossi@example.it --name "Mario Rossi" --send-email

    # non-interactive (e.g. from a script)
    python -m scripts.adduser --email ... --name "..." --password "..."

    # list users
    python -m scripts.adduser --list

    # removal
    python -m scripts.adduser --remove m.rossi@example.it

The password is never stored in clear text: only the scrypt hash ends up in
data/users.json. The data/ folder is already excluded from version control (.gitignore).
"""

import argparse
import getpass
import json
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from api.auth import create_reset_token, hash_password, reset_link, write_users
from api.filelock import file_lock
from api.mailer import send_invitation, smtp_configured
from api.privdrop import drop_to_service_user
from config.settings import USERS_FILE


def _load() -> list[dict]:
    if USERS_FILE.exists():
        try:
            data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []
    return []


def main() -> None:
    drop_to_service_user(USERS_FILE.parent)
    ap = argparse.ArgumentParser(description="Zautte user management")
    ap.add_argument("--email")
    ap.add_argument("--name")
    ap.add_argument("--password", help="if omitted: prompted (or, with --send-email, chosen by the user). "
                    "Avoid it: the password stays in the shell history and in the process list")
    ap.add_argument("--send-email", action="store_true",
                    help="email the user a single-use link to choose the password (nothing secret is sent)")
    ap.add_argument("--invite-hours", type=int, default=72, help="validity of that link (default 72)")
    ap.add_argument("--list", action="store_true", help="list users")
    ap.add_argument("--remove", metavar="EMAIL", help="remove a user")
    args = ap.parse_args()

    with file_lock(USERS_FILE):
        _run(args, ap)


def _run(args: argparse.Namespace, ap: argparse.ArgumentParser) -> None:
    users = _load()

    if args.list:
        if not users:
            print("No users registered.")
        for u in users:
            print(f"  {u.get('name', '?'):30} {u.get('email', '?'):35} (id={u.get('id')})")
        return

    if args.remove:
        email = args.remove.strip().lower()
        kept = [u for u in users if u.get("email", "").strip().lower() != email]
        if len(kept) == len(users):
            print(f"User not found: {email}")
            sys.exit(1)
        write_users(kept)
        print(f"Removed: {email}")
        return

    if not args.email or not args.name:
        ap.error("--email e --name sono obbligatori (oppure usa --list / --remove)")

    email = args.email.strip().lower()
    if args.send_email and not smtp_configured():
        print("SMTP not configured: the invitation cannot be sent. No changes made.")
        sys.exit(1)
    if args.send_email and reset_link("check") is None:
        print("Set PUBLIC_BASE_URL (or PILOT_LOGIN_URL) in .env: without it no link can be built. "
              "No changes made.")
        sys.exit(1)
    # Password: explicit, or interactive; with --send-email nobody knows it (the user sets it)
    if args.password:
        password = args.password
    elif args.send_email:
        password = secrets.token_urlsafe(24)   # unknown to everybody: replaced through the emailed link
    else:
        password = getpass.getpass("Password: ")
    if len(password) < 10:
        print("The password must be at least 10 characters long.")
        sys.exit(1)

    existing = next(
        (u for u in users if u.get("email", "").strip().lower() == email), None
    )
    if existing:
        existing["name"] = args.name
        if args.send_email:
            # the password and the open sessions stay as they are until the user follows the link
            print(f"Updated: {args.name} <{email}> (password unchanged until the link is used)")
        else:
            existing["pw_hash"] = hash_password(password)
            existing["auth_version"] = int(existing.get("auth_version", 0)) + 1   # older sessions stop working
            print(f"Updated: {args.name} <{email}>")
    else:
        users.append({
            "id": secrets.token_hex(8),
            "name": args.name,
            "email": email,
            "pw_hash": hash_password(password),
        })
        print(f"Created: {args.name} <{email}>")
    write_users(users)

    if args.send_email:
        user = existing or users[-1]
        link = reset_link(create_reset_token(user, minutes=args.invite_hours * 60))
        if link is None:
            print("  Set PUBLIC_BASE_URL (or PILOT_LOGIN_URL) in .env: without it no link can be built. "
                  "The user was saved but not invited.")
            sys.exit(1)
        try:
            send_invitation(email, args.name, link, args.invite_hours)
            print(f"  Invitation with the link to choose the password sent to {email}")
        except Exception as e:  # noqa: BLE001
            print(f"  Sending email FAILED ({e}): invitation not delivered. "
                  "Run the same command again to retry.")
            sys.exit(1)


if __name__ == "__main__":
    main()
