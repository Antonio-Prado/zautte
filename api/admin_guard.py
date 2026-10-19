"""
Controllo della chiave admin con limite ai tentativi errati.

Gli errori si contano per indirizzo IP (quello vero, dietro il bilanciatore, grazie
a FORWARDED_ALLOW_IPS). Dopo ADMIN_MAX_ATTEMPTS chiavi sbagliate nella finestra
l'indirizzo è bloccato per ADMIN_LOCKOUT_SECONDS: risposta 429 anche se la chiave
fosse giusta. Il conteggio vive in memoria e riparte da zero al riavvio del
servizio. Il localhost (sync.py, forget.py) non viene mai bloccato.
"""

import hmac
import threading
import time

from fastapi import HTTPException, Request

from config.settings import ADMIN_API_KEY, ADMIN_LOCKOUT_SECONDS, ADMIN_MAX_ATTEMPTS

_LOCAL = {"127.0.0.1", "::1"}
_lock = threading.Lock()
_failures: dict[str, list[float]] = {}   # ip -> istanti degli errori recenti
_blocked_until: dict[str, float] = {}    # ip -> fine del blocco


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _locked(ip: str, now: float) -> float:
    """Secondi di blocco rimasti per l'indirizzo (0 = libero)."""
    until = _blocked_until.get(ip, 0.0)
    if until <= now:
        _blocked_until.pop(ip, None)
        return 0.0
    return until - now


def _register_failure(ip: str, now: float) -> None:
    recent = [t for t in _failures.get(ip, []) if now - t < ADMIN_LOCKOUT_SECONDS]
    recent.append(now)
    if len(recent) >= ADMIN_MAX_ATTEMPTS:
        _blocked_until[ip] = now + ADMIN_LOCKOUT_SECONDS
        _failures.pop(ip, None)
    else:
        _failures[ip] = recent
    if len(_failures) > 10_000:  # non cresce senza limite sotto una scansione
        for k in [k for k, v in _failures.items() if now - v[-1] >= ADMIN_LOCKOUT_SECONDS]:
            del _failures[k]


def check_admin_key(request: Request, key: str | None) -> bool:
    """True se la chiave è valida. Una chiave presente ma errata viene contata;
    con l'indirizzo bloccato solleva 429 (Retry-After in secondi)."""
    if not ADMIN_API_KEY:
        return True  # autenticazione disabilitata (sviluppo)
    ip = _ip(request)
    if ip in _LOCAL:
        return bool(key) and hmac.compare_digest(key.encode(), ADMIN_API_KEY.encode())
    with _lock:
        now = time.monotonic()
        wait = _locked(ip, now)
        if wait:
            raise HTTPException(
                status_code=429,
                detail="Troppi tentativi: riprova più tardi",
                headers={"Retry-After": str(int(wait) + 1)},
            )
        if not key:
            return False  # nessun tentativo: niente da contare
        if hmac.compare_digest(key.encode(), ADMIN_API_KEY.encode()):
            _failures.pop(ip, None)
            return True
        _register_failure(ip, now)
        return False
