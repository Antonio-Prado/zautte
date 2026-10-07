"""
Cancella su richiesta le domande di un utente, o singole domande, da tutti i
file di dati e dalla memoria dell'API in esecuzione, senza riavviarla.

Uso (sul server, dalla directory del progetto; serve ADMIN_API_KEY nel .env):
    venv/bin/python -m scripts.forget --user antonio.prado@comunesbt.it --last 1
    venv/bin/python -m scripts.forget --user <email o id> --last 5
    venv/bin/python -m scripts.forget --user <email o id> --all   (anche id di utenti rimossi)
    venv/bin/python -m scripts.forget --rid 3f9a0c1d2e4b [--rid ...]
      --dry-run   mostra cosa verrebbe cancellato e si ferma
      --yes       non chiede conferma

Passa da POST /usage/forget dell'API in esecuzione: mostra le domande trovate
e, dopo la conferma, le toglie dai file e dalla memoria del processo. Il rid di una domanda si legge nel log dell'API (/var/log/chatbot.log), che
non contiene il testo, e in usage.jsonl, answers.jsonl, gaps.jsonl e feedback.jsonl.

Restano fuori: l'email di notifica di una segnalazione già inviata e, per le
domande arrivate al modello, la copia tenuta dal fornitore (Anthropic: 30
giorni con l'API diretta).
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
    """(id, nome) dell'utente indicato per email o per id."""
    try:
        users = json.loads(USERS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        users = []
    key = user.strip().lower()
    for u in users if isinstance(users, list) else []:
        if key in ((u.get("email") or "").strip().lower(), u.get("id")):
            return u["id"], u.get("name", "")
    if "@" in key:
        sys.exit(f"Utente non trovato in {USERS_FILE}: {user}")
    # Id di un utente già rimosso (adduser --remove): le sue domande restano in usage.jsonl
    print(f"Id {user} non presente in {USERS_FILE}: lo uso così com'è")
    return user.strip(), ""


def _forget(client: httpx.Client, base: str, body: dict) -> dict:
    try:
        r = client.post(f"{base}/usage/forget", json=body, headers={"X-Admin-Key": ADMIN_API_KEY})
    except httpx.HTTPError as e:
        sys.exit(f"API non raggiungibile su {base}: {e}")
    if r.status_code != 200:
        sys.exit(f"{base}: errore {r.status_code} {r.text[:200]}")
    return r.json()


def main() -> None:
    parser = argparse.ArgumentParser(description="Cancella domande dai log su richiesta")
    parser.add_argument("--user", help="email o id dell'utente")
    who = parser.add_mutually_exclusive_group()
    who.add_argument("--last", type=int, default=0, help="le ultime N domande dell'utente")
    who.add_argument("--all", action="store_true", help="tutte le domande e i feedback dell'utente")
    parser.add_argument("--rid", action="append", default=[], help="identificativo di una domanda")
    parser.add_argument("--api", default=DEFAULT_API, help=f"indirizzo dell'API (default: {DEFAULT_API})")
    parser.add_argument("--dry-run", action="store_true", help="mostra e non cancella")
    parser.add_argument("--yes", action="store_true", help="non chiede conferma")
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
        print(f"Utente: {name or uid} ({uid})")
    elif args.last or args.all:
        parser.error("--last e --all richiedono --user")
    if not selection:
        parser.error("indicare --user oppure --rid")

    with httpx.Client(timeout=30) as client:
        preview = _forget(client, args.api, {**selection, "dry_run": True})
        questions = preview["questions"]
        if not questions and not preview["feedback"]:
            print("Nessuna domanda trovata.")
            return
        print(f"Domande trovate: {len(questions)}")
        for q in questions:
            text = (q.get("q") or "(testo già scaduto)").replace("\n", " ")
            print(f"  {q.get('ts', '')[:19].replace('T', ' ')}  [{q.get('rid') or 'senza rid'}]  {text[:100]}")
        print(f"Da togliere: {preview['usage']} voci d'uso, {preview.get('answers', 0)} risposte, "
              f"{preview['gaps']} lacune, {preview['feedback']} feedback, "
              f"{preview['resolved']} marcature «risolto»")
        if args.dry_run:
            return
        if not args.yes and input("Cancellare? [s/N] ").strip().lower() not in ("s", "si", "sì", "y", "yes"):
            print("Annullato.")
            return

        # Da qui la scelta è fissata a ciò che è stato mostrato (salvo --all):
        # una domanda arrivata nel frattempo non viene presa al posto di un'altra.
        if selection.get("all"):
            fixed = selection
        else:
            fixed = {"rids": sorted(set(args.rid) | {q["rid"] for q in questions if q.get("rid")})}
            legacy_ts = [q["ts"] for q in questions if not q.get("rid")]
            if legacy_ts:
                fixed.update(uid=selection["uid"], ts=legacy_ts)

        r = _forget(client, args.api, fixed)
        print(f"Tolte {r['usage']} voci d'uso, {r.get('answers', 0)} risposte, {r['gaps']} lacune, "
              f"{r['feedback']} feedback, {r['resolved']} marcature «risolto», "
              f"{r['memory']} dalla memoria")


if __name__ == "__main__":
    main()
