"""
The document inside a signed file (`.p7m`, CAdES: a CMS SignedData envelope, RFC 5652).

Public administrations publish many PDFs signed this way: the file is the envelope, with the PDF
carried inside it as the «encapsulated content». The signature is not checked here (nothing is
trusted because of it): only the content is taken out, so its text can be read.

The envelope is BER: lengths may be definite or indefinite (closed by two zero bytes), and the
content may be one OCTET STRING or a constructed one split into pieces. A file signed more than once
(`.pdf.p7m.p7m`) holds an envelope inside the envelope: up to `_MAX_LAYERS` are opened. Base64 (PEM
or bare) envelopes are decoded first. Anything that does not parse gives None; the walk is bounded
by the size of the data and by `_MAX_DEPTH`, so a malformed file cannot make it loop.
"""
import base64
import binascii
import re

_OID_SIGNED_DATA = bytes.fromhex("2a864886f70d010702")   # 1.2.840.113549.1.7.2
_MAX_LAYERS = 4
_MAX_DEPTH = 64
_B64 = re.compile(rb"^[A-Za-z0-9+/=\s]+$")


class _BadBer(Exception):
    pass


def _header(buf: bytes, pos: int, end: int) -> tuple[int, bool, int, int | None]:
    """(tag byte, constructed, start of the content, length or None if indefinite)."""
    if pos + 2 > end:
        raise _BadBer("truncated header")
    tag = buf[pos]
    if tag & 0x1F == 0x1F:              # high tag numbers: not used by CMS envelopes
        raise _BadBer("long tag")
    first = buf[pos + 1]
    pos += 2
    if first < 0x80:
        return tag, bool(tag & 0x20), pos, first
    if first == 0x80:
        if not tag & 0x20:
            raise _BadBer("indefinite primitive")
        return tag, True, pos, None
    n = first & 0x7F
    if n > 8 or pos + n > end:
        raise _BadBer("bad length")
    length = int.from_bytes(buf[pos:pos + n], "big")
    pos += n
    if pos + length > end:
        raise _BadBer("length beyond the data")
    return tag, bool(tag & 0x20), pos, length


def _children(buf: bytes, start: int, length: int | None, end: int, depth: int) -> tuple[list, int]:
    """The elements inside a constructed value: [(tag, constructed, start, stop)], and where it ends."""
    if depth > _MAX_DEPTH:
        raise _BadBer("too deep")
    stop = end if length is None else start + length
    items = []
    pos = start
    while pos < stop:
        if length is None and buf[pos:pos + 2] == b"\x00\x00":
            return items, pos + 2
        tag, cons, cstart, clen = _header(buf, pos, stop)
        if clen is None:
            _, after = _children(buf, cstart, None, stop, depth + 1)
            items.append((tag, cons, cstart, after - 2))
            pos = after
        else:
            items.append((tag, cons, cstart, cstart + clen))
            pos = cstart + clen
    if length is None:
        raise _BadBer("missing end of contents")
    return items, stop


def _octets(buf: bytes, tag: int, cons: bool, start: int, stop: int, depth: int) -> bytes:
    """The bytes of an OCTET STRING, joining the pieces of a constructed one."""
    if not cons:
        if tag != 0x04:
            raise _BadBer("not an octet string")
        return buf[start:stop]
    if tag != 0x24:
        raise _BadBer("not an octet string")
    items, _ = _children(buf, start, stop - start, stop, depth + 1)
    return b"".join(_octets(buf, t, c, s, e, depth + 1) for t, c, s, e in items)


def _content(buf: bytes) -> bytes:
    """The encapsulated content of one SignedData envelope."""
    tag, _, start, length = _header(buf, 0, len(buf))
    if tag != 0x30:
        raise _BadBer("not a sequence")
    info, _ = _children(buf, start, length, len(buf), 0)
    if len(info) < 2 or info[0][0] != 0x06 or buf[info[0][2]:info[0][3]] != _OID_SIGNED_DATA:
        raise _BadBer("not SignedData")
    t, _, s, e = info[1]                                    # [0] EXPLICIT SignedData
    if t != 0xA0:
        raise _BadBer("no content")
    (sd,), _ = _children(buf, s, e - s, e, 1)
    signed, _ = _children(buf, sd[2], sd[3] - sd[2], sd[3], 2)
    # version, digestAlgorithms, encapContentInfo, ...
    if len(signed) < 3 or signed[2][0] != 0x30:
        raise _BadBer("no encapsulated content")
    encap, _ = _children(buf, signed[2][2], signed[2][3] - signed[2][2], signed[2][3], 3)
    if len(encap) < 2 or encap[1][0] != 0xA0:               # detached signature: no content here
        raise _BadBer("detached")
    (inner,), _ = _children(buf, encap[1][2], encap[1][3] - encap[1][2], encap[1][3], 4)
    return _octets(buf, inner[0], inner[1], inner[2], inner[3], 5)


def _decode_base64(data: bytes) -> bytes | None:
    text = data.strip()
    if text.startswith(b"-----BEGIN"):
        text = b"".join(line for line in text.splitlines() if not line.startswith(b"-----"))
    if not text or not _B64.match(text[:4096]):
        return None
    try:
        return base64.b64decode(b"".join(text.split()), validate=True)
    except (binascii.Error, ValueError):
        return None


def unwrap(data: bytes) -> bytes | None:
    """The document inside a signed envelope (opening nested envelopes), or None if `data` is not
    one. Data that is not an envelope at all (a plain PDF) also gives None."""
    opened = False
    for _ in range(_MAX_LAYERS):
        if data[:1] != b"\x30":
            decoded = _decode_base64(data) if data[:1] != b"%" else None
            if decoded is None or decoded[:1] != b"\x30":
                break
            data = decoded
        try:
            data = _content(data)
        except (_BadBer, ValueError, IndexError):
            break
        opened = True
    return data if opened else None
