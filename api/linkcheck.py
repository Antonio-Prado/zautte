"""
Links written inside the passages that lead nowhere.

A passage is given to the model as it was published: a news item of years ago may point to a
page the site has since removed, and the answer would offer that address. Before the passages
reach the model, the links in their text are tried and the dead ones are replaced by a note.

- Only links to the organisation's own sites are tried (SITE_URL, CRAWL_ALLOWED_DOMAINS), under
  the crawler's network policy (crawler/netpolicy.py): a published document cannot make the
  server call an address of its choice.
- Dead means 404 or 410 at the end of the redirects. Anything else (an error, a timeout, a
  refusal, a redirect out of the allowed sites) leaves the link as it is.
- A question waits at most LINK_CHECK_SECONDS for links never tried before; a check still
  running goes on and serves the next question. Results are remembered in memory for
  LINK_CHECK_TTL_HOURS (an unanswered check for an hour).
"""

import asyncio
import logging
import re
import time
from collections.abc import Iterable

import httpx

from config.settings import (
    CRAWL_ALLOWED_DOMAINS,
    LINK_CHECK,
    LINK_CHECK_SECONDS,
    LINK_CHECK_TTL_HOURS,
    SITE_URL,
)
from crawler.netpolicy import final_status_async, url_allowed

log = logging.getLogger(__name__)

DEAD_LINK_NOTE = "(indirizzo non più attivo)"
DEAD_STATUSES = (404, 410)
MAX_NEW_PER_QUESTION = 20     # links tried for the first time by one question
_MAX_CACHE = 5000
_UNKNOWN_SECONDS = 3600
_REQUEST_SECONDS = 8
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; Zautte/1.0)"}

# "https://..." and the bare "www.example.it/page" of older texts
_LINK_RE = re.compile(r"(?:https?://|(?<![\w/@.-])www\.)[^\s)\]>\"'*<]+", re.IGNORECASE)
_TRAILING = ".,;:!?"

_cache: dict[str, tuple[float, bool]] = {}     # address -> (expiry, dead)
_running: dict[str, asyncio.Task] = {}


def _domains() -> list[str]:
    site = httpx.URL(SITE_URL).host if SITE_URL.startswith("http") else SITE_URL
    return [d for d in [site, *CRAWL_ALLOWED_DOMAINS] if d]


def _address(link: str) -> str:
    """The address to request for a link as written in a text."""
    link = link.rstrip(_TRAILING)
    return link if link.lower().startswith("http") else "https://" + link


def links_in(text: str) -> set[str]:
    """The addresses written in a text, ready to be requested."""
    return {_address(m) for m in _LINK_RE.findall(text or "")}


def _remember(address: str, dead: bool, seconds: float) -> None:
    if len(_cache) >= _MAX_CACHE:
        now = time.monotonic()
        for k in [k for k, (expiry, _) in _cache.items() if expiry <= now] or list(_cache)[: _MAX_CACHE // 10]:
            _cache.pop(k, None)
    _cache[address] = (time.monotonic() + seconds, dead)


async def _status(address: str) -> int:
    async with httpx.AsyncClient(timeout=_REQUEST_SECONDS, headers=_HEADERS, trust_env=False) as client:
        return await final_status_async(client, address, _domains())


async def _probe(address: str) -> None:
    try:
        status = await _status(address)
    except (httpx.HTTPError, OSError, ValueError) as e:
        log.debug("Link not checked (%s): %s", type(e).__name__, address[:200])
        _remember(address, False, _UNKNOWN_SECONDS)
        return
    dead = status in DEAD_STATUSES
    if dead:
        log.info("Dead link in a passage (%d): %s", status, address[:200])
    _remember(address, dead, LINK_CHECK_TTL_HOURS * 3600)


async def dead_links(addresses: Iterable[str]) -> set[str]:
    """Those of `addresses` whose page is gone. Never raises: a link that cannot be checked
    in time is not dead."""
    now = time.monotonic()
    wanted = sorted({a for a in addresses if url_allowed(a, _domains())})
    new = [a for a in wanted if (a not in _cache or _cache[a][0] <= now) and a not in _running]
    for a in new[:MAX_NEW_PER_QUESTION]:
        task = asyncio.create_task(_probe(a))
        _running[a] = task
        task.add_done_callback(lambda _t, a=a: _running.pop(a, None))
    waiting = [_running[a] for a in wanted if a in _running]
    if waiting:
        # the checks are not cancelled at the limit: they finish and fill the cache
        await asyncio.wait(waiting, timeout=LINK_CHECK_SECONDS)
    now = time.monotonic()
    return {a for a in wanted if a in _cache and _cache[a][0] > now and _cache[a][1]}


def _without(text: str, dead: set[str]) -> str:
    def swap(m: re.Match) -> str:
        link = m.group(0)
        core = link.rstrip(_TRAILING)
        return DEAD_LINK_NOTE + link[len(core):] if _address(link) in dead else link
    return _LINK_RE.sub(swap, text)


async def without_dead_links(chunks: list[dict]) -> list[dict]:
    """The passages with the dead links of their text replaced by DEAD_LINK_NOTE. The passages
    themselves are not changed: those touched are copies."""
    if not LINK_CHECK or not chunks:
        return chunks
    dead = await dead_links({a for c in chunks for a in links_in(str(c.get("text", "")))})
    if not dead:
        return chunks
    return [{**c, "text": _without(str(c.get("text", "")), dead)} for c in chunks]
