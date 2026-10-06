"""
Limite giornaliero di domande (DAILY_QUESTION_LIMIT, default 20) su /chat e
/chat/stream.

Si conta per utente autenticato (uid del token) e per giorno di calendario del
server: a mezzanotte si riparte da zero. Con AUTH_ENABLED=false tutti sono
«anon» e si conta per indirizzo IP del client (quello vero dietro il proxy, vedi
FORWARDED_ALLOW_IPS).

I contatori stanno in memoria: c'è un solo processo uvicorn (api/serve.py). Al
primo uso si ricostruiscono da data/usage.jsonl, così un riavvio (deploy, fine
di un sync) non regala domande; le domande per IP non sono in usage.jsonl e
dopo un riavvio ripartono da zero.

Una domanda si conta quando arriva, così chi ne manda molte insieme non supera
il limite, e si restituisce se la risposta fallisce.
"""

import datetime
import json
import logging
from pathlib import Path

from config.settings import AUTH_ENABLED, DAILY_QUESTION_LIMIT, now_local

log = logging.getLogger(__name__)

_USAGE_FILE = Path(__file__).parent.parent / "data" / "usage.jsonl"

_day: str | None = None
_counts: dict[str, int] = {}


def _today() -> str:
    return now_local().date().isoformat()


def _load_today(day: str) -> dict[str, int]:
    """Domande di oggi per utente, lette da usage.jsonl (una riga per domanda
    con "ts" ISO nel fuso del server e "uid")."""
    counts: dict[str, int] = {}
    try:
        with open(_USAGE_FILE, encoding="utf-8") as fh:
            for line in fh:
                if day not in line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(e, dict) and str(e.get("ts", "")).startswith(day) and e.get("uid"):
                    key = f"uid:{e['uid']}"
                    counts[key] = counts.get(key, 0) + 1
    except FileNotFoundError:
        pass
    except OSError as e:
        log.warning("usage.jsonl non leggibile, contatori giornalieri da zero: %s", e)
    return counts


def _current() -> dict[str, int]:
    """Contatori del giorno corrente; al primo uso li legge da usage.jsonl, a
    mezzanotte li azzera."""
    global _day, _counts
    day = _today()
    if _day != day:
        _counts = _load_today(day) if _day is None else {}
        _day = day
    return _counts


def load() -> None:
    """Legge i contatori di oggi; all'avvio dell'API, in un thread (I/O su file)."""
    counts = _current()
    if counts:
        log.info("Domande di oggi ricostruite da usage.jsonl: %d utenti, %d domande",
                 len(counts), sum(counts.values()))


def key_for(user: dict, client_ip: str) -> str:
    uid = user.get("uid")
    if AUTH_ENABLED and uid and uid != "anon":
        return f"uid:{uid}"
    return f"ip:{client_ip}"


def take(key: str) -> bool:
    """Conta una domanda; False se il limite di oggi è già raggiunto."""
    if DAILY_QUESTION_LIMIT <= 0:
        return True
    counts = _current()
    if counts.get(key, 0) >= DAILY_QUESTION_LIMIT:
        return False
    counts[key] = counts.get(key, 0) + 1
    return True


def give_back(key: str) -> None:
    """Restituisce una domanda la cui risposta è fallita."""
    if DAILY_QUESTION_LIMIT <= 0:
        return
    counts = _current()
    if counts.get(key, 0) > 0:
        counts[key] -= 1


def seconds_to_midnight() -> int:
    now = now_local()
    midnight = datetime.datetime.combine(now.date() + datetime.timedelta(days=1),
                                         datetime.time(), tzinfo=now.tzinfo)
    return max(1, int((midnight - now).total_seconds()))
