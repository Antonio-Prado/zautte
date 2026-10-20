"""
Which addresses the crawler and the news sync may fetch, and how.

One policy for every download made on behalf of a page, a feed or an attachment:

- only http(s), without credentials in the address, on the default ports;
- the host must be one of the allowed domains or a subdomain of one (never a
  substring: ``www.example.it.attacker.invalid`` is not ``www.example.it``);
- no IP literals, and no host that resolves to a loopback, private or otherwise
  non-public address (CRAWL_BLOCK_PRIVATE_IPS);
- every redirect is checked again, hop by hop, before it is followed;
- the body is read in chunks and the download stops when it passes the limit
  (CRAWL_MAX_PAGE_MB for pages and feeds, CRAWL_MAX_FILE_MB for PDFs and other
  attachments).
"""

import ipaddress
import socket
import time
from collections.abc import Iterable
from urllib.parse import urlsplit

import httpx

from config.settings import (
    CRAWL_BLOCK_PRIVATE_IPS,
    CRAWL_MAX_FILE_MB,
    CRAWL_MAX_PAGE_MB,
    CRAWL_PRIVATE_OK_HOSTS,
)

_DEFAULT_PORTS = {None, 80, 443}
_FILE_TYPES = ("application/pdf", "application/octet-stream", "application/vnd",
               "application/msword", "application/zip", "application/x-")


class BlockedURL(httpx.RequestError):
    """The address is not allowed by the policy."""


class TooLarge(httpx.RequestError):
    """The body is bigger than the limit."""


def host_allowed(host: str, domains: Iterable[str]) -> bool:
    """True if `host` is one of `domains` or a subdomain of one."""
    host = host.lower().rstrip(".")
    for d in domains:
        d = d.lower().strip().rstrip(".")
        if d and (host == d or host.endswith("." + d)):
            return True
    return False


def url_allowed(url: str, domains: Iterable[str]) -> bool:
    """The shape of the URL and its host, without any network access."""
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if parts.scheme not in ("http", "https") or not host:
            return False
        if parts.username is not None or parts.password is not None:
            return False
        if parts.port not in _DEFAULT_PORTS:
            return False
    except ValueError:
        return False
    try:
        ipaddress.ip_address(host.strip("[]"))
        return False   # an IP literal is never a site we crawl
    except ValueError:
        pass
    return host_allowed(host, domains)


_CACHE_SECONDS = 60
_public_cache: dict[str, tuple[float, bool]] = {}


def _private_ok(host: str) -> bool:
    """Hosts exempted from the private-address block: exact names, never their subdomains."""
    return host.lower().rstrip(".") in CRAWL_PRIVATE_OK_HOSTS


def resolves_public(host: str) -> bool:
    """True if every address the host resolves to is a public one (cached for a minute)."""
    if not CRAWL_BLOCK_PRIVATE_IPS or _private_ok(host):
        return True
    host = host.lower().rstrip(".")
    now = time.monotonic()
    cached = _public_cache.get(host)
    if cached is None or now - cached[0] > _CACHE_SECONDS:
        try:
            infos = socket.getaddrinfo(host, None)
            ok = bool(infos) and all(
                ipaddress.ip_address(i[4][0].split("%")[0]).is_global for i in infos
            )
        except (OSError, ValueError):
            ok = False
        _public_cache[host] = cached = (now, ok)
    return cached[1]


def _check_peer(resp: httpx.Response) -> None:
    """The address the connection really went to must be public too: the name could have
    resolved elsewhere between the check and the connection (DNS rebinding). The request
    has been sent by then, but nothing of what comes back is read."""
    if not CRAWL_BLOCK_PRIVATE_IPS or _private_ok(resp.request.url.host):
        return
    stream = resp.extensions.get("network_stream")
    addr = stream.get_extra_info("server_addr") if stream is not None else None
    if addr and not ipaddress.ip_address(str(addr[0]).split("%")[0]).is_global:
        raise BlockedURL(f"connected to a non-public address: {str(resp.url)[:200]}", request=resp.request)


def _check(request: httpx.Request, domains: list[str]) -> None:
    url = str(request.url)
    if not url_allowed(url, domains) or not resolves_public(request.url.host):
        raise BlockedURL(f"address not allowed: {url[:200]}", request=request)


def guard(domains: Iterable[str]) -> dict:
    """`event_hooks` for httpx.Client: called for the first request and for every redirect."""
    allowed = list(domains)
    return {"request": [lambda request: _check(request, allowed)]}


def guard_async(domains: Iterable[str]) -> dict:
    """`event_hooks` for httpx.AsyncClient."""
    allowed = list(domains)

    async def hook(request: httpx.Request) -> None:
        _check(request, allowed)

    return {"request": [hook]}


def _limit(url: str, content_type: str) -> int:
    """Byte limit for this response: bigger for PDFs and other attachments."""
    ct = (content_type or "").lower()
    is_file = url.lower().split("?")[0].endswith((".pdf", ".doc", ".docx", ".odt", ".xls", ".xlsx", ".zip")) \
        or ct.startswith(_FILE_TYPES)
    return (CRAWL_MAX_FILE_MB if is_file else CRAWL_MAX_PAGE_MB) * 1024 * 1024


def _declared(resp: httpx.Response) -> int:
    try:
        return int(resp.headers.get("content-length") or 0)
    except ValueError:
        return 0


def _rebuilt(resp: httpx.Response, body: bytes) -> httpx.Response:
    """A normal, fully read response made from the streamed one (the body is already
    decoded, so the encoding headers are dropped)."""
    headers = [(k, v) for k, v in resp.headers.multi_items()
               if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")]
    return httpx.Response(resp.status_code, headers=headers, content=body, request=resp.request)


async def get_limited_async(client: httpx.AsyncClient, url: str) -> httpx.Response:
    """GET that stops reading when the body passes the limit (raises TooLarge)."""
    async with client.stream("GET", url) as resp:
        _check_peer(resp)
        limit = _limit(str(resp.url), resp.headers.get("content-type", ""))
        if _declared(resp) > limit:
            raise TooLarge(f"body over {limit // 1048576} MB: {url[:200]}", request=resp.request)
        body = bytearray()
        async for chunk in resp.aiter_bytes():
            body += chunk
            if len(body) > limit:
                raise TooLarge(f"body over {limit // 1048576} MB: {url[:200]}", request=resp.request)
        return _rebuilt(resp, bytes(body))


def get_limited(client: httpx.Client, url: str, **kw) -> httpx.Response:
    """Synchronous version of get_limited_async."""
    with client.stream("GET", url, **kw) as resp:
        _check_peer(resp)
        limit = _limit(str(resp.url), resp.headers.get("content-type", ""))
        if _declared(resp) > limit:
            raise TooLarge(f"body over {limit // 1048576} MB: {url[:200]}", request=resp.request)
        body = bytearray()
        for chunk in resp.iter_bytes():
            body += chunk
            if len(body) > limit:
                raise TooLarge(f"body over {limit // 1048576} MB: {url[:200]}", request=resp.request)
        return _rebuilt(resp, bytes(body))
