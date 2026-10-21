"""
Content-Security-Policy of the pages the API serves (the widget pages, the reset page, the
restricted-area login page).

The policy tells the browser which scripts may run: ours (same origin) and the inline scripts
that are in the page as served, identified by their SHA-256. A script injected by a flaw in how
an answer is drawn, or by a hostile document, has no hash in the policy and is not run, so it
cannot read the session token kept by the browser. The hashes are computed from the file as it
is on disk each time, so an installation's own pages (INSTANCE_DIR) need no change.

CSP_MODE: "enforce" (default), "report-only" (the browser logs violations in its console and
blocks nothing: to try a change on a real page) or "off". CSP_EXTRA_HOSTS adds hosts for
scripts, connections and images (the Matomo server). FRAME_ANCESTORS says who may put the
pages in a frame ('none' by default).
"""

import base64
import hashlib
import re

from config.settings import CSP_EXTRA_HOSTS, CSP_MODE, FRAME_ANCESTORS

_INLINE_SCRIPT = re.compile(r"<script(?P<attrs>[^>]*)>(?P<body>.*?)</script>", re.IGNORECASE | re.DOTALL)
_DATA_TYPES = ("application/ld+json", "application/json", "importmap")


def script_hash(source: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(source.encode("utf-8")).digest()).decode() + "'"


def inline_script_hashes(html: str) -> list[str]:
    hashes = []
    for m in _INLINE_SCRIPT.finditer(html):
        attrs = m.group("attrs")
        if re.search(r"\bsrc\s*=", attrs, re.IGNORECASE):
            continue
        if any(t in attrs.lower() for t in _DATA_TYPES):
            continue   # data, not executed
        if m.group("body").strip():
            hashes.append(script_hash(m.group("body")))
    return hashes


def header_name() -> str | None:
    if CSP_MODE == "off":
        return None
    return "Content-Security-Policy-Report-Only" if CSP_MODE == "report-only" else "Content-Security-Policy"


def policy(html: str, extra_script_hashes: list[str] | None = None, frame_ancestors: str | None = None) -> str:
    hosts = " ".join(CSP_EXTRA_HOSTS)
    scripts = " ".join(dict.fromkeys(inline_script_hashes(html) + (extra_script_hashes or [])))
    return "; ".join([
        "default-src 'none'",
        f"script-src 'self' {scripts} {hosts}".strip(),
        "style-src 'self' 'unsafe-inline'",
        f"img-src 'self' data: https: {hosts}".strip(),
        "font-src 'self' data:",
        f"connect-src 'self' https://api.github.com {hosts}".strip(),
        "manifest-src 'self'",
        "base-uri 'none'",
        "object-src 'none'",
        "form-action 'self'",
        f"frame-ancestors {frame_ancestors or FRAME_ANCESTORS}",
    ])


def headers_for(html: str, extra_script_hashes: list[str] | None = None, frame_ancestors: str | None = None) -> dict:
    name = header_name()
    return {name: policy(html, extra_script_hashes, frame_ancestors)} if name else {}
