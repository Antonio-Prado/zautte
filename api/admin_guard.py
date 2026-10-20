"""
Admin key check with a limit on wrong attempts.

Errors are counted per IP address (the real one, behind the load balancer, thanks
to FORWARDED_ALLOW_IPS). After ADMIN_MAX_ATTEMPTS wrong keys within the window
the address is blocked for ADMIN_LOCKOUT_SECONDS: 429 response even if the key
were right. The count lives in memory and restarts from zero when the
service restarts. Localhost (sync.py, forget.py) is never blocked.
"""

import hmac
import threading
import time

from fastapi import HTTPException, Request

from config.settings import (
    ADMIN_API_KEY,
    ADMIN_AUTH_DISABLED,
    ADMIN_LOCKOUT_SECONDS,
    ADMIN_MAX_ATTEMPTS,
)

_LOCAL = {"127.0.0.1", "::1"}
_lock = threading.Lock()
_failures: dict[str, list[float]] = {}   # ip -> times of recent errors
_blocked_until: dict[str, float] = {}    # ip -> end of block
_MAX_TRACKED = 10_000                    # entries kept in each table, whatever the traffic
_SWEEP_EVERY = 60.0                      # seconds between the cleanups of expired entries
_last_sweep = 0.0


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _locked(ip: str, now: float) -> float:
    """Seconds of block remaining for the address (0 = free)."""
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
    _sweep(now)


def _sweep(now: float, force: bool = False) -> None:
    """Drops the expired entries (at most once a minute) and keeps each table under
    _MAX_TRACKED, oldest first: a scan from many addresses cannot grow them without limit."""
    global _last_sweep
    if not force and now - _last_sweep < _SWEEP_EVERY and \
            max(len(_failures), len(_blocked_until)) <= _MAX_TRACKED:
        return
    _last_sweep = now
    for k in [k for k, v in _failures.items() if now - v[-1] >= ADMIN_LOCKOUT_SECONDS]:
        del _failures[k]
    for k in [k for k, until in _blocked_until.items() if until <= now]:
        del _blocked_until[k]
    for table in (_failures, _blocked_until):
        for k in list(table)[:max(0, len(table) - _MAX_TRACKED)]:
            del table[k]


def check_admin_key(request: Request, key: str | None) -> bool:
    """True if the key is valid. A key that is present but wrong is counted;
    with the address blocked it raises 429 (Retry-After in seconds)."""
    if not ADMIN_API_KEY:
        return ADMIN_AUTH_DISABLED  # no key set: closed, unless explicitly disabled (development)
    ip = _ip(request)
    if ip in _LOCAL:
        return bool(key) and hmac.compare_digest(key.encode(), ADMIN_API_KEY.encode())
    with _lock:
        now = time.monotonic()
        _sweep(now)
        wait = _locked(ip, now)
        if wait:
            raise HTTPException(
                status_code=429,
                detail="Troppi tentativi: riprova più tardi",
                headers={"Retry-After": str(int(wait) + 1)},
            )
        if not key:
            return False  # no attempt: nothing to count
        if hmac.compare_digest(key.encode(), ADMIN_API_KEY.encode()):
            _failures.pop(ip, None)
            return True
        _register_failure(ip, now)
        return False
