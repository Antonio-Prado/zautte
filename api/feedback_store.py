"""
Archivio dei feedback (pollice su/giù) con dettagli opzionali.

data/feedback.jsonl: una riga JSON per feedback —
  {id, ts, rating, question, answer_preview, uid, user, comment, urls, details_ts}

`comment` e `urls` possono arrivare insieme al voto oppure dopo, tramite
attach_details(): il collega mette 👎, poi compila il modulo nel widget con
il motivo e i link alle pagine dove sta l'informazione corretta. Così non
deve più scrivere email all'amministratore, che vede tutto in dashboard.
"""

import datetime
import json
import re
import secrets
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
FEEDBACK_FILE = DATA_DIR / "feedback.jsonl"
RESOLVED_FILE = DATA_DIR / "resolved_negative.json"   # [{key, resolved_at}]

MAX_URLS = 5
MAX_URL_LEN = 500
MAX_COMMENT_LEN = 2000
_URL_RE = re.compile(r"^https?://[^\s<>\"']+$", re.IGNORECASE)
_HOST_RE = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+(:\d{1,5})?$", re.IGNORECASE)


def now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def new_id() -> str:
    return secrets.token_hex(6)


def clean_comment(comment: str | None) -> str:
    return (comment or "").strip()[:MAX_COMMENT_LEN]


def clean_urls(urls) -> list[str]:
    """Normalizza e filtra i link: solo http/https (aggiunge https:// se manca),
    niente spazi o virgolette, niente duplicati, al massimo MAX_URLS."""
    from urllib.parse import urlsplit
    out: list[str] = []
    for u in urls or []:
        u = (u or "").strip()
        if not u:
            continue
        if "://" not in u:
            u = "https://" + u   # "www.comune.it/pagina" incollato senza schema
        if len(u) > MAX_URL_LEN or not _URL_RE.match(u):
            continue
        try:
            parts = urlsplit(u)
        except ValueError:
            continue
        if parts.scheme.lower() not in ("http", "https") or not _HOST_RE.match(parts.netloc):
            continue
        if u not in out:
            out.append(u)
        if len(out) >= MAX_URLS:
            break
    return out


def append(entry: dict) -> dict:
    """Aggiunge un feedback (assegna un id se manca) e lo ritorna."""
    FEEDBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry = {"id": new_id(), **entry}
    with open(FEEDBACK_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def iter_entries() -> list[dict]:
    if not FEEDBACK_FILE.exists():
        return []
    out = []
    with open(FEEDBACK_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def attach_details(fid: str, uid: str, comment: str, urls: list[str]) -> dict | None:
    """Allega commento e URL al feedback `fid`, solo se appartiene a `uid`.
    Riscrive il file in modo atomico. Ritorna la voce aggiornata, oppure None
    se non trovata o non sua."""
    if not fid or not FEEDBACK_FILE.exists():
        return None
    found = None
    new_lines = []
    for line in FEEDBACK_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            new_lines.append(line)
            continue
        if e.get("id") == fid and e.get("uid") == uid:
            if comment:
                e["comment"] = comment
            if urls:
                e["urls"] = urls
            e["details_ts"] = now_iso()
            found = e
            line = json.dumps(e, ensure_ascii=False)
        new_lines.append(line)
    if found is None:
        return None
    tmp = FEEDBACK_FILE.with_suffix(".tmp")
    tmp.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    tmp.replace(FEEDBACK_FILE)
    return found


def find_entry(ts: str, question: str) -> dict | None:
    """Il feedback con quel timestamp e quella domanda (l'ultimo, se più di uno)."""
    found = None
    q = (question or "")[:200]
    for e in iter_entries():
        if e.get("ts") == ts and (e.get("question") or "")[:200] == q:
            found = e
    return found


def resolved_key(ts: str, question: str) -> str:
    """Chiave con cui un feedback negativo viene marcato risolto (le voci
    precedenti al 14/09/2026 non hanno un id)."""
    return f"{ts}::{question[:200]}"


def load_resolved() -> set[str]:
    """Chiavi dei feedback negativi già marcati risolti dall'amministratore."""
    if not RESOLVED_FILE.exists():
        return set()
    try:
        return {e["key"] for e in json.loads(RESOLVED_FILE.read_text(encoding="utf-8")) if "key" in e}
    except Exception:
        return set()


def negative_open(resolved: set[str], key_fn=resolved_key, limit: int = 200,
                  include_resolved: bool = False) -> tuple[list[dict], int, int]:
    """Feedback negativi con i dettagli segnalati (autore, commento, link).
    Di default solo quelli non risolti; con include_resolved anche gli altri,
    con il campo `resolved`. Ritorna (ultimi `limit`, negativi aperti, totali)."""
    items = []
    total = 0
    open_count = 0
    for e in iter_entries():
        total += 1
        if e.get("rating") != -1:
            continue
        is_resolved = key_fn(e.get("ts", ""), e.get("question", "")) in resolved
        if not is_resolved:
            open_count += 1
        elif not include_resolved:
            continue
        items.append({
            "id": e.get("id", ""),
            "ts": e.get("ts", ""),
            "question": e.get("question", ""),
            "answer_preview": e.get("answer_preview", ""),
            "user": e.get("user", ""),
            "comment": e.get("comment", ""),
            "urls": e.get("urls", []) or [],
            "resolved": is_resolved,
        })
    return items[-limit:], open_count, total
