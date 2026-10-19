"""
Zautte user provisioning (pilot project login).

Usage:
    # create/update a user (password asked interactively, hidden)
    python -m scripts.adduser --email m.rossi@example.it --name "Mario Rossi"

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

from api.auth import hash_password
from api.mailer import send_credentials, smtp_configured
from config.settings import PILOT_LOGIN_URL, USERS_FILE


def _load() -> list[dict]:
    if USERS_FILE.exists():
        try:
            data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []
    return []


def _save(users: list[dict]) -> None:
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    USERS_FILE.write_text(
        json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Gestione utenti Zautte")
    ap.add_argument("--email")
    ap.add_argument("--name")
    ap.add_argument("--password", help="se omessa: generata (con --send-email) o chiesta")
    ap.add_argument("--send-email", action="store_true",
                    help="recapita le credenziali all'utente via SMTP")
    ap.add_argument("--login-url", help="override dell'URL di login nell'email")
    ap.add_argument("--list", action="store_true", help="elenca gli utenti")
    ap.add_argument("--remove", metavar="EMAIL", help="rimuove un utente")
    args = ap.parse_args()

    users = _load()

    if args.list:
        if not users:
            print("Nessun utente registrato.")
        for u in users:
            print(f"  {u.get('name', '?'):30} {u.get('email', '?'):35} (id={u.get('id')})")
        return

    if args.remove:
        email = args.remove.strip().lower()
        kept = [u for u in users if u.get("email", "").strip().lower() != email]
        if len(kept) == len(users):
            print(f"Utente non trovato: {email}")
            sys.exit(1)
        _save(kept)
        print(f"Rimosso: {email}")
        return

    if not args.email or not args.name:
        ap.error("--email e --name sono obbligatori (oppure usa --list / --remove)")

    email = args.email.strip().lower()
    if args.send_email and not smtp_configured():
        print("SMTP non configurato: le credenziali non si possono recapitare. Nessuna modifica.")
        sys.exit(1)
    # Password: explicit, or generated (if --send-email), or interactive
    if args.password:
        password = args.password
    elif args.send_email:
        password = secrets.token_urlsafe(12)  # generated, will be delivered by email
    else:
        password = getpass.getpass("Password: ")
    if len(password) < 8:
        print("La password deve avere almeno 8 caratteri.")
        sys.exit(1)

    pw_hash = hash_password(password)
    existing = next(
        (u for u in users if u.get("email", "").strip().lower() == email), None
    )
    if existing:
        existing["name"] = args.name
        existing["pw_hash"] = pw_hash
        print(f"Aggiornato: {args.name} <{email}>")
    else:
        users.append({
            "id": secrets.token_hex(8),
            "name": args.name,
            "email": email,
            "pw_hash": pw_hash,
        })
        print(f"Creato: {args.name} <{email}>")
    _save(users)

    if args.send_email:
        # The password is never printed: if sending fails, rerun the command
        # (without --password it generates a new one).
        url = args.login_url or PILOT_LOGIN_URL
        try:
            send_credentials(email, args.name, password, url)
            print(f"  Email con le credenziali inviata a {email}")
        except Exception as e:  # noqa: BLE001
            print(f"  Invio email FALLITO ({e}): credenziali non recapitate. "
                  "Rilancia lo stesso comando per riprovare.")
            sys.exit(1)


if __name__ == "__main__":
    main()
