"""
The pages the known facts come from, read again every day.

A curated fact (config/known_facts.json) is written from a page of the site and names it as its
"source". When the page changes, or is taken off the site, the fact goes on saying what the page
said the day it was written, and nobody is told: in one day's check four sources of 106 turned out
to be gone. This job reads every source again, keeps the text it read in KNOWN_FACTS_WATCH_FILE and
records what happened since the last time:

  - the page is gone (404 or 410);
  - the text of the page changed: the lines added and the lines taken out;
  - the page now has, or no longer has, what a fact asked to look for ("watch" in the fact, see
    api/knownfacts.py: «the programme has not been published yet» → tell when it is);
  - the page could not be read for three runs in a row.

It changes no fact: scripts/known_facts_report.py mails what is open to who keeps the facts. A page
has one open alert, with everything that changed since the facts that come from it were last
edited, and the alert stays open until one of them is edited again (or the page goes back to what
it said). The first time a page is read nothing is reported: that
text becomes what later readings are compared with (a fact written weeks ago from a page that
changed since is not caught by the first run).

Only the main text of a page is compared, without menus, header, footer and forms; lines that
change by themselves (a counter of visits) are left out with "ignore_lines"; a fact whose source
changes every day (a home page, a list of news) says "watch": false. A source that is not a page
(a PDF) is compared byte by byte.

Configuration: the optional "facts_watch" key of config/crawl_extra.json
    {"ignore_lines": ["^Numero accessi"],   # regexes of lines not compared
     "delay": 5}                            # seconds between two requests to the same site (default)
The sites read are those of the sources, which the administrator wrote in the file of the facts.

Run (daily from cron, as the sync account, through scripts/known_facts_watch.sh; the report of the
morning after tells what it found, so any hour is good: one when no sync is crawling the same sites):
    venv/bin/python -m scripts.known_facts_watch [--dry-run]
Exit codes: 0 done (also when some pages could not be read), 1 the facts or the state cannot be read.
"""

import argparse
import contextlib
import difflib
import hashlib
import json
import logging
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.privdrop import drop_to_sync_user

drop_to_sync_user()   # as root: become the sync account before config.settings reads .env

import httpx
from bs4 import BeautifulSoup

from api.knownfacts import KNOWN_FACTS_FILE, watch_targets
from config.settings import FACTS_WATCH, KNOWN_FACTS_WATCH_FILE, now_local
from crawler.netpolicy import get_limited
from scripts.council_sessions import _share_with_api

log = logging.getLogger("known_facts_watch")

DELAY_SECONDS = 5.0           # the site answers 429 after some tens of requests in a row
RETRY_WAITS = (30, 90)        # seconds waited after a 429 or a 503, then the page is asked again
GONE = (404, 410)
FAILS_BEFORE_ALERT = 3        # runs in a row a page may fail before it is reported
MAX_TEXT = 200_000            # characters of a page kept and compared
MAX_DIFF_LINES = 12           # lines added, and lines taken out, kept for the report
MAX_LINE = 300
MAX_LOG = 10                  # lines of recent history kept for each page (the alerts do not depend on it)
_DROP_TAGS = ("script", "style", "noscript", "svg", "nav", "header", "footer", "form", "iframe", "template")


def page_text(content: bytes, content_type: str, ignore: list[re.Pattern]) -> str:
    """What is compared between two readings: the main text of an HTML page, one line per block,
    without the lines to ignore; for any other content, the hash of its bytes."""
    kind = content_type.split(";")[0].strip().lower()
    if kind and "html" not in kind:
        return "sha256:" + hashlib.sha256(content).hexdigest()
    soup = BeautifulSoup(content, "html.parser")
    for tag in soup(_DROP_TAGS):
        tag.decompose()
    root = soup.find("main") or soup.find(attrs={"role": "main"}) or soup.body or soup
    lines = (re.sub(r"\s+", " ", line).strip() for line in root.get_text("\n").split("\n"))
    return "\n".join(ln for ln in lines if ln and not any(p.search(ln) for p in ignore))[:MAX_TEXT]


def changed_lines(old: str, new: str) -> tuple[list[str], list[str]]:
    """(lines added, lines taken out) between two readings, the first MAX_DIFF_LINES of each."""
    added, removed = [], []
    for line in difflib.unified_diff(old.split("\n"), new.split("\n"), lineterm="", n=0):
        if line.startswith("+") and not line.startswith("+++"):
            added.append(line[1:MAX_LINE + 1])
        elif line.startswith("-") and not line.startswith("---"):
            removed.append(line[1:MAX_LINE + 1])
    return added[:MAX_DIFF_LINES], removed[:MAX_DIFF_LINES]


def _has(pattern: str, text: str) -> bool:
    try:
        return re.search(pattern, text, re.IGNORECASE) is not None
    except re.error:
        return pattern.lower() in text.lower()   # not a regex after all: looked for as it is written


def check(previous: dict | None, status: int | None, text: str | None, today: str, target: dict,
          error: str = "") -> dict:
    """The record of a page after a reading. `status` None is a reading that failed (`error` says
    how); `text` is given with status 200.

    What the report needs is kept as the state of the page, not as a list of events that grows: the
    text the facts were last matched with (`ref_*`: the first reading, then the reading before each
    edit of a fact, told by `target["key"]`), whether today's text differs from it and how (`opened`,
    `changed`, `added`, `removed`: one open alert per page, with everything that changed since), the
    conditions of the facts that hold (`conds`), since when the page is gone, how many readings in a
    row failed. `log` is the recent history, for whoever opens the file."""
    prev = previous or {}
    key = target["key"]
    record = {k: prev.get(k) for k in ("hash", "text", "ref_key", "ref_hash", "ref_text", "opened", "changed",
                                       "gone_since")}
    record.update(status=status, checked=today, fails=0, added=list(prev.get("added") or []),
                  removed=list(prev.get("removed") or []), conds=dict(prev.get("conds") or {}),
                  log=list(prev.get("log") or []))

    def note(kind: str, **more) -> None:
        record["log"].append({"day": today, "kind": kind, **more})

    if status == 200 and text is not None:
        new_hash = hashlib.sha256(text.encode()).hexdigest()
        first = prev.get("hash") is None
        if prev.get("gone_since"):
            record["gone_since"] = None
            note("back")
        if first:
            ref_hash, ref_text = new_hash, text
            record.update(ref_key=key, opened=None, changed=None)
        else:
            ref_hash = prev.get("ref_hash") or prev["hash"]
            ref_text = prev["ref_text"] if prev.get("ref_text") is not None else (prev.get("text") or "")
            if prev.get("ref_key") != key:
                # a fact of this page was edited: whoever did it had the last reading in front, and from
                # now on the page is compared with that
                ref_hash, ref_text = prev["hash"], prev.get("text") or ""
                record.update(ref_key=key, opened=None, changed=None)
            if new_hash != prev["hash"]:
                record["changed"] = today
                note("changed")
        if new_hash != ref_hash:
            record["opened"] = record["opened"] or today
            record["changed"] = record["changed"] or today
            record["added"], record["removed"] = changed_lines(ref_text, text)
        else:
            record.update(opened=None, changed=None, added=[], removed=[])   # as it was (again)
        conds = {}
        for kind in ("appears", "disappears"):
            for pattern in target.get(kind, []):
                if _has(pattern, text) == (kind == "appears"):
                    cid = f"{kind}:{pattern}"
                    if cid not in record["conds"]:
                        note(kind, pattern=pattern)
                    # the key of the facts the day it was first seen: editing the fact closes it
                    conds[cid] = record["conds"].get(cid) or {"since": today, "first": first, "key": key}
        record.update(hash=new_hash, text=text, ref_hash=ref_hash, conds=conds,
                      ref_text=None if new_hash == ref_hash else ref_text)
    elif status in GONE:
        if not prev.get("gone_since"):
            record["gone_since"] = today
            note("gone", status=status)
    else:
        record["fails"] = int(prev.get("fails") or 0) + 1
        record["error"] = (error or f"HTTP {status}")[:200]
        if record["fails"] == FAILS_BEFORE_ALERT:
            note("unreachable", error=record["error"])
    record["log"] = record["log"][-MAX_LOG:]
    return record


def reader(client: httpx.Client, domains: list[str], ignore: list[re.Pattern], delay: float = DELAY_SECONDS,
           sleep=time.sleep, clock=time.monotonic):
    """read(url) → (status, text, error), `delay` seconds after the previous request to the same site."""
    last: dict[str, float] = {}

    def read(url: str) -> tuple[int | None, str | None, str]:
        host = urlsplit(url).netloc
        for wait in (*RETRY_WAITS, None):
            pause = delay - (clock() - last[host]) if host in last else 0
            if pause > 0:
                sleep(pause)
            try:
                r = get_limited(client, url, domains)
            except Exception as exc:  # noqa: BLE001 - network, policy or size: the page was not read
                last[host] = clock()
                return None, None, f"{type(exc).__name__}: {exc}"
            last[host] = clock()
            if r.status_code in (429, 503) and wait is not None:
                sleep(wait)
                continue
            if r.status_code != 200:
                return r.status_code, None, ""
            return 200, page_text(r.content, r.headers.get("content-type", ""), ignore), ""
        raise AssertionError("unreachable")   # the last round returns

    return read


def watch(targets: dict[str, dict], state: dict, read, today: str) -> dict:
    """The new state: one record for each page to watch (the pages no fact names any more are dropped)."""
    new = {}
    for url in sorted(targets, key=lambda u: (urlsplit(u).netloc, u)):
        status, text, error = read(url)
        prev = state.get(url) if isinstance(state.get(url), dict) else None
        new[url] = check(prev, status, text, today, targets[url], error)
    return new


def read_state(path: Path) -> dict:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(state, dict):
        raise TypeError("the state is not an object")
    return state


def write_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".watch-", dir=str(path.parent))
    try:
        _share_with_api(fd, 0o640)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def main() -> int:
    ap = argparse.ArgumentParser(description="Reads again the pages the known facts come from")
    ap.add_argument("--dry-run", action="store_true", help="read the pages and print what happened, write nothing")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)   # a line for each of a hundred requests otherwise
    try:
        entries = json.loads(KNOWN_FACTS_FILE.read_text(encoding="utf-8"))
        state = read_state(KNOWN_FACTS_WATCH_FILE)
    except FileNotFoundError:
        log.info("no %s: nothing to watch", KNOWN_FACTS_FILE.name)
        return 0
    except (OSError, ValueError, TypeError) as exc:
        log.error("the facts or the state cannot be read: %s", exc)
        return 1
    targets = watch_targets(entries if isinstance(entries, list) else [])
    if not targets:
        log.info("no fact with a source to watch")
        return 0
    ignore = []
    for pattern in FACTS_WATCH.get("ignore_lines") or []:
        try:
            ignore.append(re.compile(str(pattern), re.IGNORECASE))
        except re.error as exc:
            log.warning("facts_watch.ignore_lines: %r is not a regex (%s): left out", pattern, exc)
    today = now_local().date().isoformat()
    domains = sorted({urlsplit(u).hostname or "" for u in targets} - {""})
    with httpx.Client(timeout=40, headers={"User-Agent": "Zautte facts-watch"}, trust_env=False) as client:
        new = watch(targets, state, reader(client, domains, ignore, float(FACTS_WATCH.get("delay", DELAY_SECONDS))),
                    today)
    events = [(url, e) for url, r in new.items() for e in r["log"] if e["day"] == today]
    for url, e in events:
        log.info("%s: %s", e["kind"], url)
    if not args.dry_run:
        write_state(KNOWN_FACTS_WATCH_FILE, new)
    log.info("%d pages: %d read, %d gone, %d not read; %d first readings; %d events today%s",
             len(new), sum(1 for r in new.values() if r["status"] == 200),
             sum(1 for r in new.values() if r["gone_since"]),
             sum(1 for r in new.values() if r["fails"]),
             sum(1 for u, r in new.items() if r["status"] == 200 and not (state.get(u) or {}).get("hash")),
             len(events), " (dry run)" if args.dry_run else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
