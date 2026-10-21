"""
Which addresses the crawler and the news sync may fetch, and how.

One policy for every download made on behalf of a page, a feed or an attachment:

- only http(s), without credentials in the address, on the default ports;
- the host must be one of the allowed domains or a subdomain of one (never a
  substring: ``www.example.it.attacker.invalid`` is not ``www.example.it``);
- no IP literals, and no host that resolves to a loopback, private or otherwise
  non-public address (CRAWL_BLOCK_PRIVATE_IPS);
- the connection goes to the address that was just checked, not to a second lookup of
  the name (the Host header and the TLS name stay those of the site, so certificates
  are verified as usual): a name that changes its answer between the check and the
  connection (DNS rebinding) gets nothing;
- redirects are followed here, one hop at a time, each one checked like the first
  request;
- the body is read in chunks and decompressed in small steps: the download stops when
  the decoded size passes the limit (CRAWL_MAX_PAGE_MB for pages and feeds,
  CRAWL_MAX_FILE_MB for PDFs and other attachments), so a tiny gzip that expands to
  gigabytes never takes the memory.
"""

import ipaddress
import socket
import time
import zlib
from collections.abc import Iterable
from urllib.parse import urljoin, urlsplit

import httpx

from config.settings import (
    CRAWL_BLOCK_PRIVATE_IPS,
    CRAWL_MAX_DOWNLOAD_SECONDS,
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


class TooSlow(httpx.RequestError):
    """The download takes longer than CRAWL_MAX_DOWNLOAD_SECONDS."""


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


def _check(request: httpx.Request, domains: list[str]) -> None:
    """Safety net for any request that does not go through get_limited(): same rules."""
    if request.extensions.get("zautte_host"):
        return   # already validated and pinned by _pinned_request()
    url = str(request.url)
    if not url_allowed(url, domains) or not resolves_public(request.url.host):
        raise BlockedURL(f"address not allowed: {url[:200]}", request=request)


def guard(domains: Iterable[str]) -> dict:
    """`event_hooks` for httpx.Client."""
    allowed = list(domains)
    return {"request": [lambda request: _check(request, allowed)]}


def guard_async(domains: Iterable[str]) -> dict:
    """`event_hooks` for httpx.AsyncClient."""
    allowed = list(domains)

    async def hook(request: httpx.Request) -> None:
        _check(request, allowed)

    return {"request": [hook]}


def _addresses(host: str) -> list[str]:
    """The addresses to try for `host`, IPv4 first. All of them must be public (unless the
    host is exempted): one internal answer among public ones refuses the host."""
    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    ips = []
    for info in infos:
        ip = info[4][0].split("%")[0]
        if ip not in ips:
            ips.append(ip)
    if (CRAWL_BLOCK_PRIVATE_IPS and not _private_ok(host)
            and (not ips or not all(ipaddress.ip_address(ip).is_global for ip in ips))):
        raise OSError("non-public address")
    return sorted(ips, key=lambda ip: ":" in ip)


def _pinned_request(url: str, ip: str) -> tuple[str, dict, dict]:
    """The same request aimed at `ip`: URL, headers and extensions for httpx."""
    parts = urlsplit(url)
    return (httpx.URL(url).copy_with(host=ip).__str__(),
            {"Host": parts.hostname or "", "Accept-Encoding": "gzip, deflate"},
            {"sni_hostname": parts.hostname or "", "zautte_host": parts.hostname or ""})


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


_REDIRECTS = (301, 302, 303, 307, 308)
_MAX_HOPS = 8
_STEP = 1 << 20   # bytes of decoded data produced at a time


class _Body:
    """Accumulates a response body, decoding gzip/deflate in steps and stopping at `limit`."""

    def __init__(self, resp: httpx.Response, limit: int, deadline: float | None = None):
        self.limit, self.raw, self.out = limit, 0, bytearray()
        self.deadline = deadline
        self.request = resp.request
        enc = resp.headers.get("content-encoding", "").lower().strip()
        if enc in ("", "identity"):
            self.dec = None
        elif enc in ("gzip", "x-gzip"):
            self.dec = zlib.decompressobj(16 + zlib.MAX_WBITS)
        elif enc == "deflate":
            self.dec = zlib.decompressobj()
        else:
            raise httpx.DecodingError(f"unsupported Content-Encoding: {enc[:30]}", request=resp.request)

    def _full(self) -> TooLarge:
        return TooLarge(f"body over {self.limit // 1048576} MB", request=self.request)

    def feed(self, chunk: bytes) -> None:
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise TooSlow(f"download over {CRAWL_MAX_DOWNLOAD_SECONDS} s", request=self.request)
        self.raw += len(chunk)
        if self.raw > self.limit:
            raise self._full()
        if self.dec is None:
            self.out += chunk
            if len(self.out) > self.limit:
                raise self._full()
            return
        data = chunk
        try:
            while True:
                piece = self.dec.decompress(data, _STEP)
                self.out += piece
                if len(self.out) > self.limit:
                    raise self._full()
                data = self.dec.unconsumed_tail
                if len(piece) < _STEP and not data:
                    return
        except zlib.error as e:
            raise httpx.DecodingError(f"bad compressed body: {e}", request=self.request) from e

    def result(self) -> bytes:
        if self.dec is not None:
            try:
                self.out += self.dec.flush()
            except zlib.error as e:
                raise httpx.DecodingError(f"bad compressed body: {e}", request=self.request) from e
            if len(self.out) > self.limit:
                raise self._full()
        return bytes(self.out)


def _final(resp: httpx.Response, url: str, body: bytes) -> httpx.Response:
    """A normal, fully read response for the original (not pinned) address."""
    # raw bytes: a header such as Content-Disposition may carry non-ASCII characters
    headers = [(k, v) for k, v in resp.headers.raw
               if k.lower() not in (b"content-encoding", b"content-length", b"transfer-encoding")]
    return httpx.Response(resp.status_code, headers=headers, content=body,
                          request=httpx.Request("GET", url))


def _next_hop(resp: httpx.Response, url: str) -> str | None:
    if resp.status_code in _REDIRECTS and resp.headers.get("location"):
        return urljoin(url, resp.headers["location"])
    return None


def _plan(url: str, domains: list[str]) -> list[str]:
    """Checks the address and returns the IPs to try (see the module docstring)."""
    if not url_allowed(url, domains):
        raise BlockedURL(f"address not allowed: {url[:200]}", request=httpx.Request("GET", "http://invalid/"))
    host = urlsplit(url).hostname or ""
    try:
        return _addresses(host)
    except OSError as e:
        raise BlockedURL(f"{host}: {e}", request=httpx.Request("GET", "http://invalid/")) from e


def _limit_for(resp: httpx.Response, url: str) -> int:
    limit = _limit(url, resp.headers.get("content-type", ""))
    if _declared(resp) > limit:
        raise TooLarge(f"body over {limit // 1048576} MB: {url[:200]}", request=resp.request)
    return limit


def get_limited(client: httpx.Client, url: str, domains: Iterable[str], **kw) -> httpx.Response:
    """GET under the whole policy: checked address, pinned connection, redirects followed
    one hop at a time, bounded download. Raises BlockedURL, TooLarge or an httpx error."""
    allowed = list(domains)
    deadline = time.monotonic() + CRAWL_MAX_DOWNLOAD_SECONDS
    for _ in range(_MAX_HOPS + 1):
        last: Exception | None = None
        for ip in _plan(url, allowed):
            pinned, headers, ext = _pinned_request(url, ip)
            try:
                with client.stream("GET", pinned, headers=headers, extensions=ext,
                                   follow_redirects=False, **kw) as resp:
                    nxt = _next_hop(resp, url)
                    if nxt is None:
                        reader = _Body(resp, _limit_for(resp, url), deadline)
                        for chunk in resp.iter_raw():
                            reader.feed(chunk)
                        return _final(resp, url, reader.result())
            except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                last = e   # try the next address of the host
                continue
            break
        else:
            raise last or BlockedURL(f"no address: {url[:200]}", request=httpx.Request("GET", "http://invalid/"))
        url = nxt
    raise BlockedURL(f"too many redirects: {url[:200]}", request=httpx.Request("GET", "http://invalid/"))


async def get_limited_async(client: httpx.AsyncClient, url: str, domains: Iterable[str]) -> httpx.Response:
    """Asynchronous version of get_limited."""
    allowed = list(domains)
    deadline = time.monotonic() + CRAWL_MAX_DOWNLOAD_SECONDS
    for _ in range(_MAX_HOPS + 1):
        last: Exception | None = None
        for ip in _plan(url, allowed):
            pinned, headers, ext = _pinned_request(url, ip)
            try:
                async with client.stream("GET", pinned, headers=headers, extensions=ext,
                                         follow_redirects=False) as resp:
                    nxt = _next_hop(resp, url)
                    if nxt is None:
                        reader = _Body(resp, _limit_for(resp, url), deadline)
                        async for chunk in resp.aiter_raw():
                            reader.feed(chunk)
                        return _final(resp, url, reader.result())
            except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                last = e
                continue
            break
        else:
            raise last or BlockedURL(f"no address: {url[:200]}", request=httpx.Request("GET", "http://invalid/"))
        url = nxt
    raise BlockedURL(f"too many redirects: {url[:200]}", request=httpx.Request("GET", "http://invalid/"))
