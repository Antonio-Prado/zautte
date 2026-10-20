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
import html
import json
import logging
import os
import secrets
import threading
import time
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, HTTPException, Request, Security
from fastapi.responses import HTMLResponse
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


_RESET_TTL_MINUTES = 30


def _reset_signature(msg: str) -> str:
    """Signature of the reset links: a different input from the session tokens, so
    one kind can never pass for the other."""
    return _sign("reset:" + msg)


def _fingerprint(user: dict) -> str:
    return hashlib.sha256(user.get("pw_hash", "").encode("utf-8")).hexdigest()[:16]


def create_reset_token(user: dict) -> str:
    """Token of the password reset link. It carries a fingerprint of the current
    password hash, so it stops working as soon as the password changes (one use)."""
    payload = json.dumps(
        {"uid": user["id"], "f": _fingerprint(user),
         "exp": int(time.time()) + _RESET_TTL_MINUTES * 60},
        separators=(",", ":"),
    ).encode("utf-8")
    msg = _b64url_encode(payload)
    return f"{msg}.{_reset_signature(msg)}"


def _user_from_reset_token(token: str) -> dict | None:
    try:
        msg, sig = token.split(".", 1)
        if not hmac.compare_digest(sig, _reset_signature(msg)):
            return None
        claims = json.loads(_b64url_decode(msg))
        if int(claims.get("exp", 0)) < int(time.time()):
            return None
        user = next((u for u in _load_users() if u.get("id") == claims.get("uid")), None)
        if user is None or not hmac.compare_digest(str(claims.get("f", "")), _fingerprint(user)):
            return None
        return user
    except (ValueError, TypeError, AttributeError):
        return None


def _reset_link(request: Request, token: str) -> str:
    """Absolute link to the reset page: on the origin of PILOT_LOGIN_URL if set,
    otherwise on the one the request came to."""
    parts = urlsplit(PILOT_LOGIN_URL) if PILOT_LOGIN_URL else None
    base = f"{parts.scheme}://{parts.netloc}" if parts and parts.scheme and parts.netloc \
        else str(request.base_url).rstrip("/")
    return f"{base}/auth/reset?token={quote(token, safe='')}"


@router.post("/forgot")
@limiter.limit("5/hour")
async def forgot(request: Request, req: ForgotRequest):
    """Self-service password reset, step 1: emails a single-use link.

    Nothing changes until the link is used (otherwise anybody who knows an address
    could lock that person out). The response is always identical, whether or not
    the address exists (anti-enumeration).
    """
    generic = {
        "ok": True,
        "message": "Se l'indirizzo è registrato, riceverai un'email con il link per scegliere una nuova password.",
    }
    if not AUTH_ENABLED:
        raise HTTPException(status_code=400, detail="Autenticazione non attiva")

    user = _find_user_by_email(req.email)
    if user:
        try:
            from api.mailer import send_password_reset_link, smtp_configured
            if smtp_configured():
                send_password_reset_link(
                    user["email"], user.get("name", ""),
                    _reset_link(request, create_reset_token(user)), _RESET_TTL_MINUTES,
                )
        except Exception:
            log.warning("Password reset email not sent to %s",
                        user.get("email"), exc_info=True)
    return generic


_PAGE = """<!doctype html>
<html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><meta name="referrer" content="no-referrer">
<title>Zautte — nuova password</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;max-width:26rem;margin:12vh auto;padding:0 16px;color:#1a1a1a}}
label{{display:block;margin-top:14px}}input{{width:100%;box-sizing:border-box;padding:10px;font-size:16px;margin-top:4px}}
button{{margin-top:18px;padding:11px 16px;font-size:16px;background:#003366;color:#fff;border:0;border-radius:8px;cursor:pointer}}
.err{{color:#b3261e}}.ok{{color:#1b5e20}}</style></head>
<body><main><h1>Nuova password</h1>{body}</main></body></html>"""

_FORM = """<p>Scegli la nuova password di Zautte (almeno {min} caratteri).</p>
<form id="f"><label>Nuova password<input id="p1" type="password" autocomplete="new-password" minlength="{min}" required></label>
<label>Ripetila<input id="p2" type="password" autocomplete="new-password" minlength="{min}" required></label>
<p id="m" role="alert" class="err"></p><button type="submit">Salva la password</button></form>
{login}
<script>
const T = new URLSearchParams(location.search).get('token') || '';
document.getElementById('f').addEventListener('submit', async (e) => {{
  e.preventDefault();
  const m = document.getElementById('m'), a = p1.value, b = p2.value;
  if (a !== b) {{ m.textContent = 'Le due password non coincidono.'; return; }}
  const r = await fetch('/auth/reset', {{method: 'POST', headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{token: T, password: a}})}});
  if (r.ok) {{
    document.getElementById('f').hidden = true;
    document.querySelector('h1').textContent = 'Password aggiornata';
    m.className = 'ok'; m.textContent = 'Ora puoi accedere con la nuova password.';
    const go = document.getElementById('go'); if (go) go.hidden = false;
  }} else {{
    const d = await r.json().catch(() => ({{}}));
    m.textContent = d.detail || 'Errore. Riprova.';
  }}
}});
</script>"""

_MIN_PASSWORD = 10


def _html(body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(_PAGE.format(body=body), status_code=status,
                        headers={"Referrer-Policy": "no-referrer", "Cache-Control": "no-store"})


@router.get("/reset", response_class=HTMLResponse)
async def reset_page(token: str = ""):
    """Password reset, step 2: the page where the new password is chosen."""
    if not AUTH_ENABLED or _user_from_reset_token(token) is None:
        return _html('<p class="err">Il link non è valido o è scaduto. '
                     'Chiedine uno nuovo dalla pagina di accesso con «Password dimenticata?».</p>', 400)
    login = (f'<p id="go" hidden><a href="{html.escape(PILOT_LOGIN_URL, quote=True)}">Vai all\'accesso</a></p>'
             if PILOT_LOGIN_URL.startswith(("http://", "https://")) else "")
    return _html(_FORM.format(min=_MIN_PASSWORD, login=login))


class ResetRequest(BaseModel):
    token: str = Field(..., max_length=2000)
    password: str = Field(..., max_length=200)


@router.post("/reset")
@limiter.limit("10/hour")
async def reset_password(request: Request, req: ResetRequest):
    """Password reset, step 3: sets the chosen password; the link stops working and
    the older sessions of the user end."""
    if not AUTH_ENABLED:
        raise HTTPException(status_code=400, detail="Autenticazione non attiva")
    user = _user_from_reset_token(req.token)
    if user is None:
        raise HTTPException(status_code=400, detail="Il link non è valido o è scaduto.")
    if len(req.password) < _MIN_PASSWORD:
        raise HTTPException(status_code=400,
                            detail=f"La password deve avere almeno {_MIN_PASSWORD} caratteri.")
    if not _update_user_password(user["email"], req.password):
        raise HTTPException(status_code=500, detail="Impossibile salvare la password.")
    return {"ok": True}
