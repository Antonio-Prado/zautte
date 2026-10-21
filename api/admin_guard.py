"""
Admin key check with a limit on wrong attempts.

Errors are counted per client (the real address, behind the load balancer, thanks
to FORWARDED_ALLOW_IPS; IPv6 clients by their /64 network, see api/clientkey.py).
After ADMIN_MAX_ATTEMPTS wrong keys within the window the client is blocked for
ADMIN_LOCKOUT_SECONDS: 429 response even if the key were right. A ceiling on the
wrong keys of all clients together (ADMIN_GLOBAL_MAX_FAILURES in the same window)
stops a guessing attack spread over many networks: past it, every request from
outside is refused until the window ends. The count lives in memory and restarts
from zero when the service restarts. A right key on loopback (sync.py, forget.py, the watchdog) always passes; a wrong one is
counted like any other, so a proxy that shows every client as 127.0.0.1 does not switch the lockout off.
"""

import hmac
import threading
import time

from fastapi import HTTPException, Request

from api.clientkey import client_key
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
_MAX_TRACKED = 10_000                    # entries kept in the table of the errors, whatever the traffic
_MAX_BLOCKS = 100_000                    # blocks kept (the ones closest to expiring go first)
ADMIN_GLOBAL_MAX_FAILURES = 100          # wrong keys, all clients together, per window
_all_failures: list[float] = []          # times of the recent wrong keys of everybody
_SWEEP_EVERY = 60.0                      # seconds between the cleanups of expired entries
_sweep_state = {"last": 0.0}            # monotonic time of the last cleanup


def _ip(request: Request) -> str:
    return client_key(request)


def _is_local(request: Request) -> bool:
    """The raw peer address (not the /64 key): the sync scripts talk to the API on loopback."""
    return bool(request.client) and request.client.host in _LOCAL


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
    _all_failures.append(now)
    if len(recent) >= ADMIN_MAX_ATTEMPTS:
        _blocked_until[ip] = now + ADMIN_LOCKOUT_SECONDS
        _failures.pop(ip, None)
    else:
        _failures[ip] = recent
    _sweep(now)


def _sweep(now: float, force: bool = False) -> None:
    """Drops the expired entries (at most once a minute) and keeps each table under
    _MAX_TRACKED, oldest first: a scan from many addresses cannot grow them without limit."""
    if not force and now - _sweep_state["last"] < _SWEEP_EVERY and \
            max(len(_failures), len(_blocked_until)) <= _MAX_TRACKED:
        return
    _sweep_state["last"] = now
    for k in [k for k, v in _failures.items() if now - v[-1] >= ADMIN_LOCKOUT_SECONDS]:
        del _failures[k]
    for k in [k for k, until in _blocked_until.items() if until <= now]:
        del _blocked_until[k]
    _all_failures[:] = [t for t in _all_failures if now - t < ADMIN_LOCKOUT_SECONDS]
    for k in list(_failures)[:max(0, len(_failures) - _MAX_TRACKED)]:
        del _failures[k]
    if len(_blocked_until) > _MAX_BLOCKS:   # active blocks are kept unless there are absurdly many
        for k in sorted(_blocked_until, key=_blocked_until.get)[:len(_blocked_until) - _MAX_BLOCKS]:
            del _blocked_until[k]


MIN_KEY_LENGTH = 16


def admin_key_ok() -> bool:
    """False for an empty, short or placeholder key (the one in .env.example)."""
    return len(ADMIN_API_KEY) >= MIN_KEY_LENGTH and not ADMIN_API_KEY.lower().startswith("cambia")


def check_admin_key(request: Request, key: str | None) -> bool:
    """True if the key is valid. A key that is present but wrong is counted;
    with the address blocked it raises 429 (Retry-After in seconds)."""
    if not admin_key_ok():
        return ADMIN_AUTH_DISABLED  # no usable key: closed, unless explicitly disabled (development)
    ip = _ip(request)
    right = bool(key) and hmac.compare_digest(key.encode(), ADMIN_API_KEY.encode())
    local = _is_local(request)
    if local and right:
        return True   # the sync scripts and the watchdog, on loopback, are never held up
    with _lock:
        now = time.monotonic()
        _sweep(now)
        wait = _locked(ip, now)
        if not wait and len(_all_failures) >= ADMIN_GLOBAL_MAX_FAILURES and not local:
            wait = ADMIN_LOCKOUT_SECONDS - (now - _all_failures[0])
        if wait:
            raise HTTPException(
                status_code=429,
                detail="Troppi tentativi: riprova più tardi",
                headers={"Retry-After": str(int(wait) + 1)},
            )
        if not key:
            return False  # no attempt: nothing to count
        if right:
            _failures.pop(ip, None)
            return True
        _register_failure(ip, now)
        return False
