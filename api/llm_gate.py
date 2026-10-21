"""
Concurrency limits for the requests that call the language model.

The daily quota limits how many questions a user can ask, not how many at once: someone
(or many addresses) could open dozens of answers at the same time and saturate the
service or the provider budget. Two limits apply to /chat and /chat/stream:

- LLM_MAX_PER_USER answers at once for each user (default 2);
- LLM_MAX_CONCURRENT answers at once in the whole process (default 8).

A request over the limit is refused (429) before it uses any quota. A slot is released when
the answer ends; if that ever fails (a client that disappears) it expires after SLOT_SECONDS.
"""

import time
import uuid
from collections import Counter

from config.settings import LLM_MAX_CONCURRENT, LLM_MAX_PER_USER

SLOT_SECONDS = 300
_slots: dict[str, tuple[str, float]] = {}   # slot id -> (user, expiry)


def _expire(now: float) -> None:
    for sid in [s for s, (_, until) in _slots.items() if until <= now]:
        del _slots[sid]


def acquire(user: str) -> str | None:
    """A slot id, or None if the user or the service is at its limit. Not blocking."""
    now = time.monotonic()
    _expire(now)
    if len(_slots) >= LLM_MAX_CONCURRENT:
        return None
    if Counter(u for u, _ in _slots.values())[user] >= LLM_MAX_PER_USER:
        return None
    sid = uuid.uuid4().hex
    _slots[sid] = (user, now + SLOT_SECONDS)
    return sid


def release(sid: str | None) -> None:
    if sid:
        _slots.pop(sid, None)
