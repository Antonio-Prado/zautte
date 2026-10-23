"""
Daily question limit (DAILY_QUESTION_LIMIT, default 20) on /chat and
/chat/stream.

It is counted per authenticated user (uid from the token) and per server calendar day:
at midnight it restarts from zero. With AUTH_ENABLED=false everyone is
«anon» and it is counted by client IP address (the real one behind the proxy, see
FORWARDED_ALLOW_IPS).

Counters are kept in memory: there is a single uvicorn process (api/serve.py). On
first use they are rebuilt from data/usage.jsonl, so a restart (deploy, end
of a sync) does not hand out free questions; per-IP questions are not in usage.jsonl and
restart from zero after a restart.

A question is counted when it arrives, so someone sending many at once does not exceed
the limit, and it is given back if the answer fails.
"""

import datetime
import json
import logging
from pathlib import Path

from config.settings import AUTH_ENABLED, DAILY_LIMIT_EXEMPT, DAILY_QUESTION_LIMIT, now_local

log = logging.getLogger(__name__)

_USAGE_FILE = Path(__file__).parent.parent / "data" / "usage.jsonl"

# Counters for the current day only: {ISO day: {key: questions}}
_by_day: dict[str, dict[str, int]] = {}


def _today() -> str:
    return now_local().date().isoformat()


def _load_today(day: str) -> dict[str, int]:
    """Today's questions per user, read from usage.jsonl (one line per question
    with ISO "ts" in the server timezone and "uid")."""
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
        pass  # no question recorded so far: start from zero
    except OSError as e:
        log.warning("usage.jsonl not readable, daily counters start from zero: %s", e)
    return counts


def _current() -> dict[str, int]:
    """Counters for the current day; on first use it reads them from usage.jsonl, at
    midnight it resets them."""
    day = _today()
    counts = _by_day.get(day)
    if counts is None:
        counts = _load_today(day) if not _by_day else {}
        _by_day.clear()
        _by_day[day] = counts
    return counts


def load() -> None:
    """Reads today's counters; at API startup, in a thread (file I/O)."""
    counts = _current()
    if counts:
        log.info("Today's questions rebuilt from usage.jsonl: %d users, %d questions",
                 len(counts), sum(counts.values()))


def exempt(user: dict) -> bool:
    """True for an account listed in DAILY_LIMIT_EXEMPT (by id, name or e-mail): no daily
    limit. The session token carries the id (`uid`) and the name; users.json rows carry
    `id`, `name` and `email`: all are checked."""
    if not DAILY_LIMIT_EXEMPT:
        return False
    return any(str(user.get(k) or "").strip().lower() in DAILY_LIMIT_EXEMPT
               for k in ("uid", "id", "name", "email"))


def limit_for(user: dict) -> int:
    """The daily limit told to this user: 0 (no limit) for an exempt account."""
    return 0 if exempt(user) else DAILY_QUESTION_LIMIT


def key_for(user: dict, client_ip: str) -> str:
    uid = user.get("uid")
    if AUTH_ENABLED and uid and uid != "anon":
        return f"uid:{uid}"
    return f"ip:{client_ip}"


def take(key: str) -> bool:
    """Counts a question; False if today's limit has already been reached."""
    if DAILY_QUESTION_LIMIT <= 0:
        return True
    counts = _current()
    if counts.get(key, 0) >= DAILY_QUESTION_LIMIT:
        return False
    counts[key] = counts.get(key, 0) + 1
    return True


def give_back(key: str) -> None:
    """Gives back a question whose answer failed."""
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
