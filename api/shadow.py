"""
Shadow answers: a second model answers the same questions, unseen by the users, so that an
administrator can compare it with the one in service before switching (SHADOW_OPENAI=true).

When the model in service is asked a question, the very same messages also go, in the
background, to the model of the OpenAI-compatible API (OPENAI_BASE_URL, OPENAI_MODEL), asked as
it would be in service (OPENAI_ANSWER_NOTE after the question, OPENAI_MAX_TOKENS). The user
gets the answer of the model in service only, and never waits for the other. The shadow answer
goes through the same phone guard and the same masking as a logged answer and is written to
data/shadow.jsonl with its times and tokens, next to the times of the answer in service; the
restricted area shows the two side by side (GET /shadow).

The file holds answers to users' questions: it expires with RETENTION_USAGE_TEXT_DAYS
(scripts/purge_logs.py) and deletion on request removes its entries too (api/datalog.py).
Questions that get no model call (cache, fixed answers, nothing found) are not shadowed.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, Callable

from api import openai_llm
from api.datalog import SHADOW_FILE
from api.filelock import file_lock
from api.pii import redact
from api.safefile import dump_line
from config.settings import (
    LLM_PROVIDER,
    OPENAI_BASE_URL,
    OPENAI_MAX_TOKENS,
    OPENAI_MODEL,
    OPENAI_PRICE_IN,
    OPENAI_PRICE_OUT,
    SHADOW_OPENAI,
    now_local,
)

log = logging.getLogger(__name__)

_MAX_AT_ONCE = 2          # shadow answers being written at the same time; one more is skipped
_running: set[asyncio.Task] = set()


def enabled() -> bool:
    """On only when asked for, with a second model to call and another one in service."""
    return bool(SHADOW_OPENAI and LLM_PROVIDER != "openai" and OPENAI_BASE_URL and OPENAI_MODEL)


def _write(entry: dict) -> None:
    try:
        SHADOW_FILE.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(SHADOW_FILE), open(SHADOW_FILE, "a", encoding="utf-8") as fh:
            fh.write(dump_line(entry) + "\n")
    except (OSError, TypeError, ValueError) as exc:
        log.warning("Shadow log not written: %s", exc)


def _entry(kind: str, rid: str, uid: str, **fields) -> dict:
    return {"ts": now_local().isoformat(timespec="seconds"), "kind": kind, "rid": rid, "uid": uid,
            **{k: v for k, v in fields.items() if v is not None}}


def _wanted(rid: str | None, uid: str | None) -> bool:
    return bool(enabled() and rid and uid and uid != "anon")


def start(rid: str | None, uid: str | None, messages: list[dict],
          guard: Callable[[str], str] | None = None) -> None:
    """Sends `messages` to the shadow model in the background; returns at once. `guard` is the
    check applied to the text of an answer before a user sees it (the phone guard)."""
    if not _wanted(rid, uid):
        return
    if len(_running) >= _MAX_AT_ONCE:
        log.info("Question %s: shadow answer skipped (%d already running)", rid, len(_running))
        return
    task = asyncio.create_task(_answer(str(rid), str(uid), messages, guard))
    _running.add(task)
    task.add_done_callback(_running.discard)


async def _answer(rid: str, uid: str, messages: list[dict], guard: Callable[[str], str] | None) -> None:
    started = time.monotonic()
    first_ms = None
    parts: list[str] = []
    result: dict = {}
    error = None
    try:
        # as this model would be asked in service: its own reminder after the question, its own room
        async for text in openai_llm.chat_stream(openai_llm.with_answer_note(messages), OPENAI_MODEL,
                                                 temperature=0.3, max_tokens=OPENAI_MAX_TOKENS,
                                                 result=result):
            if first_ms is None and text.strip():
                first_ms = int((time.monotonic() - started) * 1000)
            parts.append(text)
    except Exception as exc:  # noqa: BLE001 - a failed comparison must never touch the service
        error = str(exc)[:200] or type(exc).__name__
        log.warning("Question %s: shadow answer failed: %s", rid, error)
    answer = "".join(parts).strip()
    if answer and guard is not None:
        try:
            answer = guard(answer)
        except Exception:   # same reason
            log.warning("Question %s: guard failed on the shadow answer", rid, exc_info=True)
    usage = result.get("usage") or {}
    tokens_in, tokens_out = usage.get("in"), usage.get("out")
    cost = (round((tokens_in * OPENAI_PRICE_IN + tokens_out * OPENAI_PRICE_OUT) / 1_000_000, 6)
            if usage else None)
    entry = _entry("shadow", rid, uid, model=OPENAI_MODEL, a=redact(answer)[:20000],
                   first_ms=first_ms, total_ms=int((time.monotonic() - started) * 1000),
                   tokens_in=tokens_in, tokens_out=tokens_out, cost=cost,
                   finish=result.get("finish"), error=error)
    await asyncio.to_thread(_write, entry)


def note_primary(rid: str | None, uid: str | None, model: str, total_ms: int,
                 first_ms: int | None = None) -> None:
    """Times of the answer in service, for the comparison (its text is in answers.jsonl)."""
    if _wanted(rid, uid):
        _write(_entry("primary", str(rid), str(uid), model=model, first_ms=first_ms, total_ms=total_ms))


async def timed(gen: AsyncGenerator[str, None], rid: str | None, uid: str | None,
                model: str) -> AsyncGenerator[str, None]:
    """The streamed answer in service, unchanged, with its times noted when it ends."""
    if not _wanted(rid, uid):
        async for text in gen:
            yield text
        return
    started = time.monotonic()
    first_ms = None
    done = False
    try:
        async for text in gen:
            if first_ms is None and text.strip():
                first_ms = int((time.monotonic() - started) * 1000)
            yield text
        done = True
    finally:
        if done:   # an interrupted answer has no total worth comparing
            total_ms = int((time.monotonic() - started) * 1000)
            await asyncio.to_thread(note_primary, rid, uid, model, total_ms, first_ms)


def _median(values: list[int]) -> int | None:
    values = sorted(values)
    if not values:
        return None
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) // 2


def comparisons(shadow: list[dict], usage: list[dict], answers: list[dict],
                names: dict[str, str], limit: int = 50, offset: int = 0) -> dict:
    """What the restricted area shows: the questions that have a shadow answer, most recent
    first, each with the answer in service, and a summary over all of them. The arguments are
    the entries of shadow.jsonl, usage.jsonl and answers.jsonl and the users' names by id."""
    shadows = {e["rid"]: e for e in shadow if e.get("kind") == "shadow" and e.get("rid")}
    primaries = {e["rid"]: e for e in shadow if e.get("kind") == "primary" and e.get("rid")}
    questions = {e["rid"]: e for e in usage if e.get("rid")}
    given = {e["rid"]: e for e in answers if e.get("rid")}

    items = []
    for rid, s in shadows.items():
        q, a, p = questions.get(rid, {}), given.get(rid, {}), primaries.get(rid, {})
        items.append({
            "rid": rid, "ts": q.get("ts") or s.get("ts", ""),
            "user": names.get(s.get("uid", ""), s.get("uid", "")),
            "q": q.get("q", ""),
            "primary": {"model": p.get("model", ""), "a": a.get("a", ""), "partial": bool(a.get("partial")),
                        "first_ms": p.get("first_ms"), "total_ms": p.get("total_ms")},
            "shadow": {k: s.get(k) for k in ("model", "a", "first_ms", "total_ms", "tokens_in",
                                              "tokens_out", "cost", "finish", "error")},
        })
    items.sort(key=lambda i: i["ts"], reverse=True)

    ok = [i for i in items if not i["shadow"].get("error")]
    both = [i for i in ok if i["primary"]["total_ms"] is not None and i["shadow"]["total_ms"] is not None]
    summary = {
        "enabled": enabled(),
        "shadow_model": OPENAI_MODEL,
        "total": len(items),
        "errors": len(items) - len(ok),
        "timed": len(both),
        "primary_median_ms": _median([i["primary"]["total_ms"] for i in both]),
        "shadow_median_ms": _median([i["shadow"]["total_ms"] for i in both]),
        "primary_first_median_ms": _median([i["primary"]["first_ms"] for i in both
                                            if i["primary"]["first_ms"] is not None]),
        "shadow_first_median_ms": _median([i["shadow"]["first_ms"] for i in both
                                           if i["shadow"]["first_ms"] is not None]),
        "shadow_faster": sum(i["shadow"]["total_ms"] < i["primary"]["total_ms"] for i in both),
        "shadow_cost": round(sum(i["shadow"].get("cost") or 0 for i in items), 4),
    }
    return {"summary": summary, "items": items[offset:offset + max(0, limit)]}
