"""
Rewriting of the data files containing user questions (data/).

Used by the automatic expiry (scripts/purge_logs.py) and by deletion on
request (POST /usage/forget, scripts/forget.py).

Each question has an identifier `rid` (12 hexadecimal characters) written in
usage.jsonl, answers.jsonl, gaps.jsonl, feedback.jsonl and in the API log: it is the
precise way to find it everywhere. Entries recorded before 05/10/2026 do not have it and
are matched by user, text and time.
"""

import datetime as dt
import json
import os
from collections.abc import Callable, Iterable
from pathlib import Path

from api.feedback_store import resolved_key
from api.filelock import file_lock
from config.settings import DATA_DIR

USAGE_FILE = DATA_DIR / "usage.jsonl"
ANSWERS_FILE = DATA_DIR / "answers.jsonl"
GAPS_FILE = DATA_DIR / "gaps.jsonl"
FEEDBACK_FILE = DATA_DIR / "feedback.jsonl"
RESOLVED_FILE = DATA_DIR / "resolved_negative.json"

# Entries without rid: the gap is written before the model's answer and the usage
# entry after (up to a couple of minutes later); the feedback comes after the question.
_LEGACY_WINDOW = dt.timedelta(minutes=5)


def parse_ts(ts: str) -> dt.datetime | None:
    try:
        t = dt.datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.astimezone()   # local time if naive


def replace_keeping_owner(path: Path, tmp: Path) -> None:
    """Replaces `path` with `tmp`, keeping owner and permissions."""
    st = path.stat()
    os.chmod(tmp, st.st_mode & 0o7777)
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        os.chown(tmp, st.st_uid, st.st_gid)
    tmp.replace(path)


def rewrite_jsonl(path: Path, transform: Callable[[dict], dict | None],
                  dry_run: bool) -> tuple[int, int]:
    """Rewrites a JSONL file applying `transform` to each entry (None = delete).
    Returns (entries deleted, entries modified). Non-JSON lines are kept.

    The API appends lines to the same files: the whole read-and-replace happens under
    the lock of the file (api/filelock.py), so no line can arrive in between and be lost."""
    if not path.exists():
        return 0, 0
    with file_lock(path):
        return _rewrite_jsonl(path, transform, dry_run)


def _rewrite_jsonl(path: Path, transform: Callable[[dict], dict | None],
                   dry_run: bool) -> tuple[int, int]:
    with open(path, encoding="utf-8") as f:
        content = f.read()
        size = f.tell()
    removed = changed = 0
    out: list[str] = []
    for line in content.splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            out.append(line)
            continue
        if not isinstance(entry, dict):
            out.append(line)
            continue
        new = transform(dict(entry))
        if new is None:
            removed += 1
            continue
        if new != entry:
            changed += 1
            line = json.dumps(new, ensure_ascii=False)
        out.append(line)
    if dry_run or not (removed or changed):
        return removed, changed
    tmp = path.with_name(path.name + ".purge.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        if out:
            f.write("\n".join(out) + "\n")
        with open(path, encoding="utf-8") as src:
            src.seek(size)
            f.write(src.read())   # lines appended during the rewrite
    replace_keeping_owner(path, tmp)
    return removed, changed


def drop_resolved(drop: Callable[[str], bool], dry_run: bool) -> int:
    """Removes from resolved_negative.json the «risolto» marks whose key
    ("<ts del feedback>::<domanda>") satisfies `drop`. Returns how many."""
    if not RESOLVED_FILE.exists():
        return 0
    try:
        items = json.loads(RESOLVED_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    if not isinstance(items, list):
        return 0
    keep = [i for i in items if not (isinstance(i, dict) and drop(str(i.get("key", ""))))]
    removed = len(items) - len(keep)
    if removed and not dry_run:
        tmp = RESOLVED_FILE.with_name(RESOLVED_FILE.name + ".purge.tmp")
        tmp.write_text(json.dumps(keep, ensure_ascii=False, indent=2), encoding="utf-8")
        replace_keeping_owner(RESOLVED_FILE, tmp)
    return removed


def answer_owner(rid: str) -> str | None:
    """User id the answer `rid` was given to (from answers.jsonl); None if unknown."""
    for e in _read_jsonl(ANSWERS_FILE):
        if e.get("rid") == rid:
            return e.get("uid")
    return None


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(e, dict):
                out.append(e)
    return out


def _close_in_time(a: str, b: str) -> bool:
    ta, tb = parse_ts(a), parse_ts(b)
    return ta is not None and tb is not None and abs(ta - tb) <= _LEGACY_WINDOW


def forget(*, rids: Iterable[str] = (), uid: str | None = None, last: int = 0,
           every: bool = False, ts: Iterable[str] = (), dry_run: bool = False) -> dict:
    """Deletes the chosen questions from usage.jsonl, answers.jsonl, gaps.jsonl and
    feedback.jsonl, with the «risolto» marks of the removed feedback.

    Selection: by `rids`, or by user `uid` with the last `last` questions,
    all of them (`every`, together with all their feedback) or those with the `ts`
    timestamps of usage.jsonl. Entries without rid (before 05/10/2026) are matched
    by text and time; those of anonymous visitors are only in gaps.jsonl and
    can only be found by rid.

    Returns the questions found (ts, rid, q) and how many entries would be removed, or
    were removed, from each file. With dry_run it changes nothing."""
    rids = {r for r in rids if r}
    ts_set = set(ts)
    usage = _read_jsonl(USAGE_FILE)

    picked_idx = {
        i for i, e in enumerate(usage)
        if (e.get("rid") and e["rid"] in rids)
        or (uid and e.get("uid") == uid and (every or e.get("ts") in ts_set))
    }
    if uid and last > 0:
        picked_idx.update([i for i, e in enumerate(usage) if e.get("uid") == uid][-last:])
    picked = [usage[i] for i in sorted(picked_idx)]

    all_rids = rids | {e["rid"] for e in picked if e.get("rid")}
    legacy = [e for e in picked if not e.get("rid")]
    legacy_keys = {(e.get("uid"), e.get("ts")) for e in legacy}

    def drop_usage(e: dict) -> dict | None:
        if e.get("rid"):
            return None if e["rid"] in all_rids else e
        return None if (e.get("uid"), e.get("ts")) in legacy_keys else e

    only_gaps: list[dict] = []   # questions from anonymous users: no usage entry, only the gap
    found_rids = {e.get("rid") for e in picked}

    def drop_gap(e: dict) -> dict | None:
        rid = e.get("rid")
        if rid:
            if rid not in all_rids:
                return e
            if rid not in found_rids:
                only_gaps.append(e)
            return None
        query = (e.get("query") or "").strip()
        for u in legacy:
            if query == (u.get("q") or "").strip()[:200] and _close_in_time(e.get("ts", ""), u.get("ts", "")):
                return None
        return e

    dropped_feedback: list[dict] = []

    def drop_feedback(e: dict) -> dict | None:
        rid = e.get("rid")
        hit = (rid and rid in all_rids) or (every and uid and e.get("uid") == uid)
        if not hit and not rid:
            fb_ts = parse_ts(e.get("ts", ""))
            for u in legacy:
                u_ts = parse_ts(u.get("ts", ""))
                if (e.get("uid") == u.get("uid") and e.get("question") == (u.get("q") or "")[:200]
                        and fb_ts is not None and u_ts is not None
                        and fb_ts >= u_ts - _LEGACY_WINDOW):
                    hit = True
                    break
        if hit:
            dropped_feedback.append(e)
            return None
        return e

    n_usage, _ = rewrite_jsonl(USAGE_FILE, drop_usage, dry_run) if (all_rids or legacy) else (0, 0)
    # Answers (since 07/10/2026) always have the rid: no matching by text and time.
    n_answers, _ = (rewrite_jsonl(ANSWERS_FILE, lambda e: None if e.get("rid") in all_rids else e,
                                  dry_run) if all_rids else (0, 0))
    n_gaps, _ = rewrite_jsonl(GAPS_FILE, drop_gap, dry_run) if (all_rids or legacy) else (0, 0)
    n_feedback, _ = (rewrite_jsonl(FEEDBACK_FILE, drop_feedback, dry_run)
                     if (all_rids or legacy or (every and uid)) else (0, 0))

    keys = {resolved_key(e.get("ts", ""), e.get("question", "")) for e in dropped_feedback}
    n_resolved = drop_resolved(lambda k: k in keys, dry_run) if keys else 0

    questions = [{"ts": e.get("ts", ""), "rid": e.get("rid", ""), "q": e.get("q", "")}
                 for e in picked]
    questions += [{"ts": e.get("ts", ""), "rid": e.get("rid", ""), "q": e.get("query", "")}
                  for e in only_gaps]
    return {"questions": questions, "usage": n_usage, "answers": n_answers, "gaps": n_gaps,
            "feedback": n_feedback, "resolved": n_resolved}
