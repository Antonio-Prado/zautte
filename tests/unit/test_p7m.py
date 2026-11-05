"""Signed envelopes (.p7m): the document inside is taken out, malformed data gives None."""
import base64

from indexer.p7m import unwrap

PDF = b"%PDF-1.4\n" + b"x" * 3000 + b"\n%%EOF\n"
OID_SIGNED_DATA = bytes.fromhex("06092a864886f70d010702")
OID_DATA = bytes.fromhex("06092a864886f70d010701")


def tlv(tag: int, body: bytes, indefinite: bool = False) -> bytes:
    if indefinite:
        return bytes([tag, 0x80]) + body + b"\x00\x00"
    n = len(body)
    if n < 0x80:
        return bytes([tag, n]) + body
    size = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([tag, 0x80 | len(size)]) + size + body


def envelope(content: bytes, indefinite: bool = False, chunk: int = 0) -> bytes:
    """A minimal SignedData with `content` encapsulated (no signer: not needed to take it out)."""
    if chunk:
        pieces = b"".join(tlv(0x04, content[i:i + chunk]) for i in range(0, len(content), chunk))
        octets = tlv(0x24, pieces, indefinite)
    else:
        octets = tlv(0x04, content)
    encap = tlv(0x30, OID_DATA + tlv(0xA0, octets, indefinite), indefinite)
    signed = tlv(0x30, tlv(0x02, b"\x01") + tlv(0x31, b"") + encap + tlv(0x31, b""), indefinite)
    return tlv(0x30, OID_SIGNED_DATA + tlv(0xA0, signed, indefinite), indefinite)


def test_definite_and_indefinite_lengths():
    assert unwrap(envelope(PDF)) == PDF
    assert unwrap(envelope(PDF, indefinite=True)) == PDF


def test_content_split_in_pieces():
    assert unwrap(envelope(PDF, chunk=1000)) == PDF
    assert unwrap(envelope(PDF, indefinite=True, chunk=1000)) == PDF


def test_nested_envelopes_and_base64():
    triple = envelope(envelope(envelope(PDF), indefinite=True))
    assert unwrap(triple) == PDF
    pem = b"-----BEGIN PKCS7-----\n" + base64.encodebytes(envelope(PDF)) + b"-----END PKCS7-----\n"
    assert unwrap(pem) == PDF
    assert unwrap(base64.b64encode(envelope(PDF))) == PDF


def test_not_an_envelope_or_malformed():
    assert unwrap(PDF) is None
    assert unwrap(b"") is None
    assert unwrap(b"\x30\x80\x00") is None
    assert unwrap(b"\x30\x82\xff\xff" + b"\x00" * 10) is None
    assert unwrap(envelope(PDF)[:-500]) is None                   # truncated
    assert unwrap(tlv(0x30, OID_DATA + tlv(0xA0, tlv(0x04, PDF)))) is None   # not SignedData
    deep = b"\x30\x80" * 5000                                     # nesting without end: bounded
    assert unwrap(deep) is None
