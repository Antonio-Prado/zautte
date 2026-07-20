"""
Provisioning utenti Zautte (login del progetto pilota).

Uso:
    # crea/aggiorna un utente (password chiesta in modo interattivo, nascosta)
    python -m scripts.adduser --email m.rossi@comunesbt.it --name "Mario Rossi"

    # non interattivo (es. da script)
    python -m scripts.adduser --email ... --name "..." --password "..."

    # elenco utenti
    python -m scripts.adduser --list

    # rimozione
    python -m scripts.adduser --remove m.rossi@comunesbt.it

La password non viene mai salvata in chiaro: in data/users.json finisce solo
l'hash scrypt. Il file data/ è già escluso dal versionamento (.gitignore).
"""

import argparse
import getpass
import json
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from api.auth import hash_password          # noqa: E402
from config.settings import USERS_FILE      # noqa: E402


def _load() -> list[dict]:
    if USERS_FILE.exists():
        try:
            data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
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
    ap.add_argument("--password", help="se omessa, viene chiesta in modo interattivo")
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
    password = args.password or getpass.getpass("Password: ")
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


if __name__ == "__main__":
    main()
