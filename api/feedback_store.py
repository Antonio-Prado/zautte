"""
Store of feedback (thumbs up/down) with optional details.

data/feedback.jsonl: one JSON line per feedback —
  {id, ts, rating, question, answer_preview, uid, user, comment, urls, details_ts}

`comment` and `urls` can arrive together with the vote or afterwards, through
attach_details(): the colleague gives a 👎, then fills in the form in the widget with
the reason and the links to the pages where the correct information is. This way they no longer
have to write emails to the administrator, who sees everything in the dashboard.
"""

import json
import re
import secrets
from pathlib import Path

from api.filelock import file_lock
from api.safefile import write_text_atomic
from config.settings import now_local

DATA_DIR = Path(__file__).parent.parent / "data"
FEEDBACK_FILE = DATA_DIR / "feedback.jsonl"
RESOLVED_FILE = DATA_DIR / "resolved_negative.json"   # [{key, resolved_at}]

MAX_URLS = 5
MAX_URL_LEN = 500
MAX_COMMENT_LEN = 2000
_URL_RE = re.compile(r"^https?://[^\s<>\"']+$", re.IGNORECASE)
_HOST_RE = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+(:\d{1,5})?$", re.IGNORECASE)


def now_iso() -> str:
    return now_local().isoformat(timespec="seconds")


def new_id() -> str:
    return secrets.token_hex(6)


def clean_comment(comment: str | None) -> str:
    return (comment or "").strip()[:MAX_COMMENT_LEN]


def clean_urls(urls) -> list[str]:
    """Normalises and filters the links: only http/https (adds https:// if missing),
    no spaces or quotes, no duplicates, at most MAX_URLS."""
    from urllib.parse import urlsplit
    out: list[str] = []
    for u in urls or []:
        u = (u or "").strip()
        if not u:
            continue
        if "://" not in u:
            u = "https://" + u   # "www.comune.it/pagina" pasted without a scheme
        if len(u) > MAX_URL_LEN or not _URL_RE.match(u):
            continue
        try:
            parts = urlsplit(u)
        except ValueError:
            continue
        if parts.scheme.lower() not in ("http", "https") or not _HOST_RE.match(parts.netloc):
            continue
        if u not in out:
            out.append(u)
        if len(out) >= MAX_URLS:
            break
    return out


def vote_key(entry: dict) -> str | None:
    """«One vote per user per answer» key: uid::rid. None for entries
    without a user or without rid (anonymous or before 05/10/2026), which
    stay one per line."""
    uid, rid = entry.get("uid"), entry.get("rid")
    return f"{uid}::{rid}" if uid and rid else None


def dedup(entries: list[dict]) -> list[dict]:
    """One vote per user and answer: if the same uid::rid pair appears
    several times the LAST one wins (change of mind or repeated clicks on the thumb; until
    11/10/2026 every click added a line). The order stays that of the file."""
    last: dict[str, int] = {}
    for i, e in enumerate(entries):
        k = vote_key(e)
        if k:
            last[k] = i
    return [e for i, e in enumerate(entries)
            if (k := vote_key(e)) is None or last[k] == i]


def append(entry: dict) -> dict:
    with file_lock(FEEDBACK_FILE):
        return _append(entry)


def _append(entry: dict) -> dict:
    """Records a feedback and returns it with its id. If the same user has
    already voted on the same answer (uid::rid), the new vote REPLACES the
    old one in its line, keeping its id; otherwise it is appended at the end."""
    FEEDBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
    key = vote_key(entry)
    if key and FEEDBACK_FILE.exists():
        lines = FEEDBACK_FILE.read_text(encoding="utf-8").splitlines()
        for i in range(len(lines) - 1, -1, -1):
            try:
                old = json.loads(lines[i])
            except json.JSONDecodeError:
                continue
            if vote_key(old) == key:
                entry = {"id": old.get("id") or new_id(), **entry}
                lines[i] = json.dumps(entry, ensure_ascii=False)
                write_text_atomic(FEEDBACK_FILE, "\n".join(lines) + "\n")
                return entry
    entry = {"id": new_id(), **entry}
    with open(FEEDBACK_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def iter_entries() -> list[dict]:
    if not FEEDBACK_FILE.exists():
        return []
    out = []
    with open(FEEDBACK_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                # truncated or corrupted line (e.g. interrupted write): skipped
                pass
    return out


def attach_details(fid: str, uid: str, comment: str, urls: list[str]) -> dict | None:
    with file_lock(FEEDBACK_FILE):
        return _attach_details(fid, uid, comment, urls)


def _attach_details(fid: str, uid: str, comment: str, urls: list[str]) -> dict | None:
    """Attaches comment and URLs to feedback `fid`, only if it belongs to `uid`.
    Rewrites the file atomically. Returns the updated entry, or None
    if not found or not theirs."""
    if not fid or not FEEDBACK_FILE.exists():
        return None
    found = None
    new_lines = []
    for line in FEEDBACK_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            new_lines.append(line)
            continue
        if e.get("id") == fid and e.get("uid") == uid:
            if comment:
                e["comment"] = comment
            if urls:
                e["urls"] = urls
            e["details_ts"] = now_iso()
            found = e
            line = json.dumps(e, ensure_ascii=False)
        new_lines.append(line)
    if found is None:
        return None
    write_text_atomic(FEEDBACK_FILE, "\n".join(new_lines) + "\n")
    return found


def find_entry(ts: str, question: str) -> dict | None:
    """The feedback with that timestamp and that question (the last one, if more than one)."""
    found = None
    q = (question or "")[:200]
    for e in iter_entries():
        if e.get("ts") == ts and (e.get("question") or "")[:200] == q:
            found = e
    return found


def resolved_key(ts: str, question: str) -> str:
    """Key by which a negative feedback is marked as resolved (entries
    before 14/09/2026 have no id)."""
    return f"{ts}::{question[:200]}"


def load_resolved() -> set[str]:
    """Keys of the negative feedback already marked as resolved by the administrator."""
    if not RESOLVED_FILE.exists():
        return set()
    try:
        return {e["key"] for e in json.loads(RESOLVED_FILE.read_text(encoding="utf-8")) if "key" in e}
    except (OSError, ValueError, TypeError):
        return set()


def negative_open(resolved: set[str], key_fn=resolved_key, limit: int = 200,
                  include_resolved: bool = False) -> tuple[list[dict], int, int]:
    """Negative feedback with the reported details (author, comment, links).
    By default only the unresolved ones; with include_resolved also the others,
    with the `resolved` field. Returns (last `limit`, open negatives, totals)."""
    items = []
    total = 0
    open_count = 0
    for e in dedup(iter_entries()):
        total += 1
        if e.get("rating") != -1:
            continue
        is_resolved = key_fn(e.get("ts", ""), e.get("question", "")) in resolved
        if not is_resolved:
            open_count += 1
        elif not include_resolved:
            continue
        items.append({
            "id": e.get("id", ""),
            "ts": e.get("ts", ""),
            "question": e.get("question", ""),
            "answer_preview": e.get("answer_preview", ""),
            "user": e.get("user", ""),
            "comment": e.get("comment", ""),
            "urls": e.get("urls", []) or [],
            "resolved": is_resolved,
        })
    return items[-limit:], open_count, total
