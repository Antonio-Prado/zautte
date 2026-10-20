"""
Conversation memory kept by the server.

The last turns of a conversation used to be sent back by the browser with every
question, so nothing proved that a message with the role "assistant" had been
written by the assistant. Now the server remembers the turns itself: the browser
only holds the conversation id (`cid`) that the server gave it.

In memory only: a restart or two hours without a question end the conversation
(the next question just has no context). Each conversation belongs to one user.
"""

import secrets
import time
from collections import OrderedDict

MAX_MESSAGES = 6          # last 3 turns
MAX_CHARS = 4000          # per message
TTL_SECONDS = 2 * 3600
MAX_CONVERSATIONS = 5000

_store: OrderedDict[tuple[str, str], tuple[float, list[dict]]] = OrderedDict()


def _expire(now: float) -> None:
    while _store:
        key, (ts, _) = next(iter(_store.items()))
        if now - ts <= TTL_SECONDS and len(_store) <= MAX_CONVERSATIONS:
            break
        del _store[key]


def resolve(uid: str, cid: str | None) -> tuple[str, list[dict] | None]:
    """The conversation id to use and its history. An unknown id, an expired one
    or one that belongs to another user starts a new conversation."""
    now = time.time()
    _expire(now)
    entry = _store.get((uid, cid)) if cid else None
    if entry is None:
        return secrets.token_hex(8), None
    return cid, [dict(m) for m in entry[1]]


def add_turn(uid: str, cid: str, question: str, answer: str) -> None:
    """Remembers a completed turn."""
    now = time.time()
    messages = list(_store.get((uid, cid), (0, []))[1])
    messages += [{"role": "user", "content": question[:MAX_CHARS]},
                 {"role": "assistant", "content": answer[:MAX_CHARS]}]
    _store[(uid, cid)] = (now, messages[-MAX_MESSAGES:])
    _store.move_to_end((uid, cid))
    _expire(now)


def forget(uid: str) -> None:
    """Drops the conversations of a user (deletion on request)."""
    for key in [k for k in _store if k[0] == uid]:
        del _store[key]
