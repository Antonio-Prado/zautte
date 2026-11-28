"""
The dates a known fact may carry (config/known_facts.json).

A curated fact is true when it is written and stays in the file until somebody takes it out:
«the programme has not been published yet» went on being told the day after the programme came
out. Two optional fields say when a fact has to be looked at again:

    "expires":   "2026-12-13"   # the last day the fact is used; from the day after it is left out
    "review_by": "2026-12-02"   # the day by which somebody checks it again; the fact stays in use

`expires` is for what ends with the date (a fair, a call for applications): after it the answer
comes from the pages again. `review_by` is for what goes on being useful but may have changed (a
deadline that has passed inside a fact about how to pay). scripts/known_facts_report.py mails the
facts whose dates are near or past. Shared by the API (which only needs `expires`) and that script.
"""

import hashlib
import json
from datetime import date
from pathlib import Path

KNOWN_FACTS_FILE = Path(__file__).parent.parent / "config" / "known_facts.json"
DATE_FIELDS = ("expires", "review_by")


def fact_date(entry: dict, field: str) -> date | None:
    """The date of `field`, None when the fact has none. ValueError when it is there but is not a
    date written YYYY-MM-DD."""
    value = entry.get(field)
    if value is None or value == "":
        return None
    try:
        return date.fromisoformat(value.strip())
    except (ValueError, AttributeError, TypeError):   # not a date, or not even a string
        raise ValueError(f"{field} is not a date written YYYY-MM-DD: {value!r}") from None


def is_expired(expires: date | None, today: date) -> bool:
    """`expires` is the last day of use: the fact is out from the day after."""
    return expires is not None and today > expires


def watch_targets(entries: list[dict]) -> dict[str, dict]:
    """The pages to read again for the facts (scripts/known_facts_watch.py): for each address the
    titles of the facts that come from it, what they ask to look for and a key that changes when one
    of those facts is edited (an alert stays open until then).

    A fact is watched at its "source" unless it says otherwise:
        "watch": false                        # not watched (a home page, a list that changes every day)
        "watch": {"url": "https://…",         # another page than the source (default: the source)
                  "appears": "regex",         # tell when the page has this (what the fact says is not there yet)
                  "disappears": "regex"}      # tell when the page no longer has this
    """
    targets: dict[str, dict] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        watch = entry.get("watch")
        if watch is False:
            continue
        watch = watch if isinstance(watch, dict) else {}
        url = str(watch.get("url") or entry.get("source") or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            continue
        target = targets.setdefault(url, {"titles": [], "indexes": [], "appears": [], "disappears": [], "keys": []})
        target["titles"].append(" ".join(str(entry.get("title", "")).split()))
        target["indexes"].append(index)   # where the facts are in `entries`
        for kind in ("appears", "disappears"):
            if isinstance(watch.get(kind), str) and watch[kind].strip():
                target[kind].append(watch[kind].strip())
        target["keys"].append(hashlib.sha256(
            json.dumps(entry, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest())
    for target in targets.values():
        target["key"] = hashlib.sha256("".join(sorted(target.pop("keys"))).encode()).hexdigest()[:16]
    return targets
