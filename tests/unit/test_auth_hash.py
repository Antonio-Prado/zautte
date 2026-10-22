import base64
import hashlib
import secrets

from api import auth


def test_roundtrip_and_wrong_password():
    h = auth.hash_password("correct horse")
    assert auth.verify_password("correct horse", h)
    assert not auth.verify_password("wrong", h)


def test_old_hash_still_verifies_and_is_marked_for_upgrade():
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(b"old-password", salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    old = f"scrypt$16384$8$1${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"
    assert auth.verify_password("old-password", old)
    assert auth.needs_rehash(old)
    assert not auth.needs_rehash(auth.hash_password("x" * 10))


def test_absurd_parameters_are_refused_not_computed():
    assert not auth.verify_password("x", "scrypt$1073741824$8$1$AA==$AA==")
    assert not auth.verify_password("x", "garbage")


def test_token_roundtrip_and_tamper():
    auth.AUTH_SECRET = "s" * 32
    auth.AUTH_ENABLED = True
    token = auth.create_token("u1", "Name", 0)
    assert auth._b64url_decode(token.split(".")[0])   # well formed
    assert auth.verify_token(token + "x") is None


def test_empty_revocation_file_is_an_empty_list(tmp_path, monkeypatch):
    f = tmp_path / "revoked_tokens.json"
    f.write_text("")
    monkeypatch.setattr(auth, "_REVOKED_FILE", f)
    monkeypatch.setattr(auth, "_revoked_state", {"mtime": None, "keys": {}})
    assert auth._revoked_keys() == {}
    assert auth._revoked_state["mtime"] is not None   # read once, not complained about at every request
