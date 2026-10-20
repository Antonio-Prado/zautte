"""
Zautte user authentication.

Email + password login for a restricted group (pilot project). The session
token is STATELESS and signed with HMAC-SHA256: any uvicorn worker
validates it without shared state (in production two processes ran, IPv4 and IPv6,
so a file-based session store would cause race conditions and "lost" logins).

No external dependencies: standard library only.
Logout revokes the token (data/revoked_tokens.json) and a password change ends the
older sessions of that user (pw_changed in users.json).
Users are provisioned in data/users.json (see scripts/adduser.py):
    [{"id": "...", "name": "Mario Rossi", "email": "...", "pw_hash": "scrypt$..."}]
The password is never stored in clear text: only the scrypt hash.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time

from fastapi import APIRouter, HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from api.limiter import limiter
from config.settings import (
    AUTH_ENABLED,
    AUTH_SECRET,
    AUTH_TOKEN_TTL_DAYS,
    PILOT_LOGIN_URL,
    USERS_FILE,
)

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Password hashing (scrypt, standard library)
# ---------------------------------------------------------------------------
_SCRYPT_N = 2 ** 14      # ~16 MB of memory: below the default maxmem (32 MB)
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32


def hash_password(password: str) -> str:
    """Returns a self-describing string 'scrypt$N$r$p$salt$hash' (base64)."""
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(
        password.encode("utf-8"), salt=salt,
        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_SCRYPT_DKLEN,
    )
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N, _SCRYPT_R, _SCRYPT_P,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(dk).decode("ascii"),
    )


def verify_password(password: str, stored: str) -> bool:
    """Constant-time comparison against a hash produced by hash_password()."""
    try:
        scheme, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        dk = hashlib.scrypt(
            password.encode("utf-8"), salt=salt,
            n=int(n), r=int(r), p=int(p), dklen=len(expected),
        )
        return hmac.compare_digest(dk, expected)
    except (ValueError, TypeError):  # stored hash malformed: fail closed
        return False


# "Decoy" hash computed once at import: by always verifying the password
# (even when the email does not exist) the response times do not reveal which
# emails are registered.
_DUMMY_HASH = hash_password(secrets.token_hex(16))


# ---------------------------------------------------------------------------
# Signed session token (HMAC-SHA256, compact JWT-like format)
# ---------------------------------------------------------------------------
def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(msg: str) -> str:
    sig = hmac.new(AUTH_SECRET.encode("utf-8"), msg.encode("ascii"), hashlib.sha256).digest()
    return _b64url_encode(sig)


def create_token(uid: str, name: str) -> str:
    now = int(time.time())
    exp = now + AUTH_TOKEN_TTL_DAYS * 86400
    payload = json.dumps(
        {"uid": uid, "name": name, "iat": now, "exp": exp, "jti": secrets.token_hex(8)},
        separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    msg = _b64url_encode(payload)
    return f"{msg}.{_sign(msg)}"


def verify_token(token: str) -> dict | None:
    """Returns the claims if the signature is valid, the token is not expired or
    revoked (logout), the user still exists and the password has not been
    changed since the token was issued; otherwise None."""
    try:
        msg, sig = token.split(".", 1)
        if not hmac.compare_digest(sig, _sign(msg)):
            return None
        claims = json.loads(_b64url_decode(msg))
        if int(claims.get("exp", 0)) < int(time.time()):
            return None
        if revocation_key(claims) in _revoked_keys():
            return None
        users = _users_cached()
        if users:  # an unreadable user file must not lock everybody out
            user = next((u for u in users if u.get("id") == claims.get("uid")), None)
            if user is None or int(claims.get("iat", 0)) < int(user.get("pw_changed", 0)):
                return None
        return claims
    except (ValueError, TypeError, AttributeError):  # malformed token
        return None


# ---------------------------------------------------------------------------
# Revoked tokens (logout): data/revoked_tokens.json, {key: exp}
# ---------------------------------------------------------------------------
_REVOKED_FILE = USERS_FILE.parent / "revoked_tokens.json"
_revoked_lock = threading.Lock()
_revoked: dict[str, int] = {}
_revoked_mtime: float | None = None


def revocation_key(claims: dict) -> str:
    """Identifier of a token in the revocation list (tokens issued before the
    jti claim existed are identified by user and expiry)."""
    return str(claims.get("jti") or f"{claims.get('uid')}:{claims.get('exp')}")


def _revoked_keys() -> dict[str, int]:
    """The revocation list, reloaded when the file changes."""
    global _revoked, _revoked_mtime
    try:
        mtime = os.stat(_REVOKED_FILE).st_mtime
    except OSError:
        return _revoked
    if mtime != _revoked_mtime:
        try:
            data = json.loads(_REVOKED_FILE.read_text(encoding="utf-8"))
            _revoked = {str(k): int(v) for k, v in data.items()} if isinstance(data, dict) else {}
            _revoked_mtime = mtime
        except (OSError, ValueError, TypeError):
            pass
    return _revoked


def revoke_token(claims: dict) -> None:
    """Adds the token to the revocation list until its natural expiry."""
    now = int(time.time())
    with _revoked_lock:
        current = {k: v for k, v in _revoked_keys().items() if v > now}
        current[revocation_key(claims)] = int(claims.get("exp", now))
        tmp = _REVOKED_FILE.with_suffix(".tmp")
        _REVOKED_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(current), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, _REVOKED_FILE)


# ---------------------------------------------------------------------------
# User store (data/users.json)
# ---------------------------------------------------------------------------
def _load_users() -> list[dict]:
    if not USERS_FILE.exists():
        return []
    try:
        data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


_users_cache: tuple[float, list[dict]] | None = None


def _users_cached() -> list[dict]:
    """The user list, reread only when users.json changes (checked on every request)."""
    global _users_cache
    try:
        mtime = os.stat(USERS_FILE).st_mtime
    except OSError:
        return []
    if _users_cache is None or _users_cache[0] != mtime:
        _users_cache = (mtime, _load_users())
    return _users_cache[1]


def get_user_by_id(uid: str) -> dict | None:
    """User (id, name, email) matching the token id; None if it does not exist."""
    if not uid:
        return None
    for u in _load_users():
        if u.get("id") == uid:
            return {"id": u["id"], "name": u.get("name", ""), "email": u.get("email", "")}
    return None


def _find_user_by_email(email: str) -> dict | None:
    email = email.strip().lower()
    for u in _load_users():
        if u.get("email", "").strip().lower() == email:
            return u
    return None


def _update_user_password(email: str, new_password: str) -> bool:
    """Updates the password hash of the user with the given email. True if updated."""
    email = email.strip().lower()
    users = _load_users()
    changed = False
    for u in users:
        if u.get("email", "").strip().lower() == email:
            u["pw_hash"] = hash_password(new_password)
            u["pw_changed"] = int(time.time())  # older sessions stop working
            changed = True
    if changed:
        try:
            USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
            USERS_FILE.write_text(
                json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except (OSError, TypeError, ValueError):
            return False
    return changed


# ---------------------------------------------------------------------------
# Dependency: identity of the request's user
# ---------------------------------------------------------------------------
log = logging.getLogger(__name__)
_bearer = HTTPBearer(auto_error=False)


async def require_user(
    cred: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> dict:
    """
    Dependency to protect the user endpoints.
    - AUTH_ENABLED=false → authentication disabled: identity 'anon'.
    - AUTH_ENABLED=true  → requires a valid 'Authorization: Bearer <token>' token.
    """
    if not AUTH_ENABLED:
        return {"uid": "anon", "name": "anonimo"}
    if cred is None or (cred.scheme or "").lower() != "bearer" or not cred.credentials:
        raise HTTPException(
            status_code=401, detail="Autenticazione richiesta",
            headers={"WWW-Authenticate": "Bearer"},
        )
    claims = verify_token(cred.credentials)
    if claims is None:
        raise HTTPException(
            status_code=401, detail="Sessione non valida o scaduta",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return claims


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------
class LoginRequest(BaseModel):
    email: str = Field(..., max_length=200)
    password: str = Field(..., max_length=200)


class LoginResponse(BaseModel):
    token: str
    name: str
    expires_in: int


@router.post("/login", response_model=LoginResponse)
@limiter.limit("10/minute")
async def login(request: Request, req: LoginRequest):
    if not AUTH_ENABLED:
        raise HTTPException(status_code=400, detail="Autenticazione non attiva")
    if not AUTH_SECRET:
        raise HTTPException(status_code=500, detail="AUTH_SECRET non configurato")

    user = _find_user_by_email(req.email)
    stored = user["pw_hash"] if user else _DUMMY_HASH
    ok = verify_password(req.password, stored)
    if not user or not ok:
        raise HTTPException(status_code=401, detail="Email o password non validi")

    token = create_token(user["id"], user.get("name", user["email"]))
    return LoginResponse(
        token=token,
        name=user.get("name", ""),
        expires_in=AUTH_TOKEN_TTL_DAYS * 86400,
    )


@router.post("/logout")
async def logout(user: dict = Security(require_user)):
    """Ends the session: this token stops working even before it expires."""
    if user.get("exp"):
        revoke_token(user)
    return {"ok": True}


@router.get("/me")
async def me(user: dict = Security(require_user)):
    """Returns the identity of the authenticated user."""
    return {"uid": user.get("uid"), "name": user.get("name")}


class ForgotRequest(BaseModel):
    email: str = Field(..., max_length=200)


@router.post("/forgot")
@limiter.limit("5/hour")
async def forgot(request: Request, req: ForgotRequest):
    """Self-service password reset: generates a new password and sends it by email.

    The response is always identical, whether or not the address exists (anti-enumeration).
    Passwords are hashed (scrypt): the original cannot be recovered, it can
    only be reset.
    """
    generic = {
        "ok": True,
        "message": "Se l'indirizzo è registrato, riceverai una nuova password via email.",
    }
    if not AUTH_ENABLED:
        raise HTTPException(status_code=400, detail="Autenticazione non attiva")

    user = _find_user_by_email(req.email)
    if user:
        new_pw = secrets.token_urlsafe(12)
        if _update_user_password(user["email"], new_pw):
            try:
                from api.mailer import send_password_reset, smtp_configured
                if smtp_configured():
                    send_password_reset(
                        user["email"], user.get("name", ""), new_pw, PILOT_LOGIN_URL
                    )
            except Exception:
                log.warning("Password reset email not sent to %s",
                            user.get("email"), exc_info=True)
    return generic
