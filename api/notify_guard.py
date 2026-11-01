"""
Limits on the e-mails the API sends to the administrator on behalf of users.

A pilot user can send many 👎 with a comment (and details on the same feedback), and anyone can
send applications: without a limit each one is an e-mail to the administrator's mailbox and
through the relay. The data is always stored and shown in the restricted area; only the e-mail
is skipped when a limit is reached:
- feedback: at most one e-mail per feedback in FEEDBACK_NOTIFY_GAP_SECONDS, and at most
  FEEDBACK_NOTIFY_MAX_PER_HOUR in all;
- applications: at most APPLY_NOTIFY_MAX_PER_HOUR.
In memory (one API process): a restart starts the counts again, which errs on the side of e-mail.
"""

from __future__ import annotations

import threading
import time

from config.settings import (
    APPLY_NOTIFY_MAX_PER_HOUR,
    FEEDBACK_NOTIFY_GAP_SECONDS,
    FEEDBACK_NOTIFY_MAX_PER_HOUR,
)

_HOUR = 3600.0
_MAX_TRACKED = 10_000

_lock = threading.Lock()
_last_by_feedback: dict[str, float] = {}
_sent: dict[str, list[float]] = {"feedback": [], "application": []}


def _within_hour_cap(kind: str, cap: int, now: float) -> bool:
    recent = [t for t in _sent[kind] if now - t < _HOUR]
    _sent[kind] = recent
    if cap > 0 and len(recent) >= cap:
        return False
    recent.append(now)
    return True


def feedback_ok(feedback_id: str, now: float | None = None) -> bool:
    """True if the e-mail for this feedback may be sent now (and counts it)."""
    now = time.monotonic() if now is None else now
    with _lock:
        last = _last_by_feedback.get(feedback_id)
        if last is not None and now - last < FEEDBACK_NOTIFY_GAP_SECONDS:
            return False
        if not _within_hour_cap("feedback", FEEDBACK_NOTIFY_MAX_PER_HOUR, now):
            return False
        _last_by_feedback[feedback_id] = now
        if len(_last_by_feedback) > _MAX_TRACKED:
            for k in sorted(_last_by_feedback, key=_last_by_feedback.get)[:len(_last_by_feedback) - _MAX_TRACKED]:
                del _last_by_feedback[k]
        return True


def application_ok(now: float | None = None) -> bool:
    """True if the e-mail for a new application may be sent now (and counts it)."""
    now = time.monotonic() if now is None else now
    with _lock:
        return _within_hour_cap("application", APPLY_NOTIFY_MAX_PER_HOUR, now)
