"""
Limits per account, on top of the limits per client address.

An attacker with many addresses (or a whole IPv6 prefix) is not slowed by a limit per
address, so passwords of known accounts could be sprayed freely and the reset email
could be triggered again and again, which also hurts the sending reputation of the
organisation's domain. Here the wrong passwords of each account are counted: after
LOGIN_MAX_FAILURES within LOGIN_LOCK_SECONDS the account refuses logins (with the same
answer as a wrong password, so nothing is revealed) until that time has passed, and a
correct login clears the count. Reset emails are limited to RESET_EMAILS_PER_HOUR per
account; the others are dropped silently. Counts live in memory (they restart with the
service) and the tables are bounded, oldest entries first.
"""

import threading
import time

from config.settings import LOGIN_LOCK_SECONDS, LOGIN_MAX_FAILURES, RESET_EMAILS_PER_HOUR

_MAX_TRACKED = 20_000
_lock = threading.Lock()
_failures: dict[str, list[float]] = {}
_reset_sent: dict[str, list[float]] = {}


def _key(email: str) -> str:
    return (email or "").strip().lower()[:200]


def _recent(table: dict[str, list[float]], key: str, window: float, now: float) -> list[float]:
    times = [t for t in table.get(key, []) if now - t < window]
    if times:
        table[key] = times
    else:
        table.pop(key, None)
    return times


def _bound(table: dict[str, list[float]]) -> None:
    for k in list(table)[:max(0, len(table) - _MAX_TRACKED)]:
        del table[k]


def login_locked(email: str) -> bool:
    with _lock:
        return len(_recent(_failures, _key(email), LOGIN_LOCK_SECONDS, time.monotonic())) >= LOGIN_MAX_FAILURES


def login_failed(email: str) -> None:
    """Counts a wrong password (also for an address that is not registered, so nobody can tell)."""
    with _lock:
        now = time.monotonic()
        times = _recent(_failures, _key(email), LOGIN_LOCK_SECONDS, now)
        times.append(now)
        _failures[_key(email)] = times
        _bound(_failures)


def login_ok(email: str) -> None:
    with _lock:
        _failures.pop(_key(email), None)


def reset_email_allowed(email: str) -> bool:
    """True and counted if this account may get another reset email within the hour."""
    with _lock:
        now = time.monotonic()
        times = _recent(_reset_sent, _key(email), 3600.0, now)
        if len(times) >= RESET_EMAILS_PER_HOUR:
            return False
        times.append(now)
        _reset_sent[_key(email)] = times
        _bound(_reset_sent)
        return True
