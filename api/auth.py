"""
Zautte user authentication.

Email + password login for a restricted group (pilot project). The session
token is STATELESS and signed with HMAC-SHA256: any uvicorn worker
validates it without shared state (in production two processes ran, IPv4 and IPv6,
so a file-based session store would cause race conditions and "lost" logins).

No external dependencies: standard library only.
Logout revokes the token (data/revoked_tokens.json) and a password change ends the
older sessions of that user (auth_version in users.json, repeated in the token).
Users are provisioned in data/users.json (see scripts/adduser.py):
    [{"id": "...", "name": "Mario Rossi", "email": "...", "pw_hash": "scrypt$..."}]
The password is never stored in clear text: only the scrypt hash.
"""

from __future__ import annotations

import asyncio
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

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Security
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from api import account_guard, csp
from api.filelock import file_lock
from api.limiter import limiter
from config.settings import (
    AUTH_ENABLED,
    AUTH_SECRET,
    AUTH_TOKEN_TTL_DAYS,
    PILOT_LOGIN_URL,
    PUBLIC_BASE_URL,
    USERS_FILE,
)

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Password hashing (scrypt, standard library)
# ---------------------------------------------------------------------------
_SCRYPT_N = 2 ** 15      # ~32 MB of memory per check; older hashes (2**14) are upgraded at login
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SCRYPT_MAXMEM = 96 * 1024 * 1024
_SCRYPT_MAX_N = 2 ** 17  # a stored hash asking for more is treated as damaged, not computed


def hash_password(password: str) -> str:
    """Returns a self-describing string 'scrypt$N$r$p$salt$hash' (base64)."""
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(
        password.encode("utf-8"), salt=salt,
        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_SCRYPT_DKLEN,
        maxmem=_SCRYPT_MAXMEM,
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
        if not 2 <= int(n) <= _SCRYPT_MAX_N or int(r) > 16 or int(p) > 4:
            return False
        dk = hashlib.scrypt(
            password.encode("utf-8"), salt=salt,
            n=int(n), r=int(r), p=int(p), dklen=len(expected),
            maxmem=_SCRYPT_MAXMEM,
        )
        return hmac.compare_digest(dk, expected)
    except (ValueError, TypeError):  # stored hash malformed: fail closed
        return False


def needs_rehash(stored: str) -> bool:
    """True if the stored hash was made with weaker parameters than the current ones."""
    try:
        return int(stored.split("$")[1]) < _SCRYPT_N
    except (IndexError, ValueError):
        return False


def _upgrade_hash(uid: str, password: str, old_hash: str) -> None:
    """After a successful login, store the password again with the current parameters.
    Skipped if the hash changed in the meantime (reset, new password)."""
    new_hash = hash_password(password)   # slow: done before taking the lock
    with file_lock(USERS_FILE):
        users = _load_users()
        for u in users:
            if u.get("id") == uid and u.get("pw_hash") == old_hash:
                u["pw_hash"] = new_hash
                try:
                    write_users(users)
                except (OSError, TypeError, ValueError):
                    log.warning("Could not upgrade a password hash")
                return


# "Decoy" hash computed once at import: by always verifying the password
# (even when the email does not exist) the response times do not reveal which
# emails are registered.
_DUMMY_HASH = hash_password(secrets.token_hex(16))

# At most this many password checks at once, each in a worker thread (not in the event loop)
_password_slots = asyncio.Semaphore(4)


# ---------------------------------------------------------------------------
# Signed session token (HMAC-SHA256, compact JWT-like format)
# ---------------------------------------------------------------------------
def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


_MIN_SECRET = 16


def auth_secret_ok() -> bool:
    return len(AUTH_SECRET) >= _MIN_SECRET


def _sign(msg: str) -> str:
    if not auth_secret_ok():
        raise ValueError("AUTH_SECRET is missing or too short")   # never sign or verify with a weak key
    sig = hmac.new(AUTH_SECRET.encode("utf-8"), msg.encode("ascii"), hashlib.sha256).digest()
    return _b64url_encode(sig)


def create_token(uid: str, name: str, auth_version: int = 0) -> str:
    now = int(time.time())
    exp = now + AUTH_TOKEN_TTL_DAYS * 86400
    payload = json.dumps(
        {"uid": uid, "name": name, "iat": now, "exp": exp, "jti": secrets.token_hex(8),
         "av": auth_version},
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
        user = next((u for u in _users_cached() or [] if u.get("id") == claims.get("uid")), None)
        if user is None or int(claims.get("av", 0)) != int(user.get("auth_version", 0)):
            return None  # unknown user, empty or unreadable user file, password changed: closed
        if int(claims.get("iat", 0)) < int(user.get("pw_changed", 0)):
            return None  # password changed before auth_version existed (users.json of old installations)
        return claims
    except (ValueError, TypeError, AttributeError):  # malformed token
        return None


# ---------------------------------------------------------------------------
# Revoked tokens (logout): data/revoked_tokens.json, {key: exp}
# ---------------------------------------------------------------------------
_REVOKED_FILE = USERS_FILE.parent / "revoked_tokens.json"
_revoked_lock = threading.Lock()
_revoked_state: dict = {"mtime": None, "keys": {}}


def revocation_key(claims: dict) -> str:
    """Identifier of a token in the revocation list (tokens issued before the
    jti claim existed are identified by user and expiry)."""
    return str(claims.get("jti") or f"{claims.get('uid')}:{claims.get('exp')}")


def _revoked_keys() -> dict[str, int]:
    """The revocation list, reloaded when the file changes."""
    st = _revoked_state
    try:
        mtime = os.stat(_REVOKED_FILE).st_mtime
    except OSError:
        return st["keys"]
    if mtime != st["mtime"]:
        try:
            data = json.loads(_REVOKED_FILE.read_text(encoding="utf-8"))
            st["keys"] = {str(k): int(v) for k, v in data.items()} if isinstance(data, dict) else {}
            st["mtime"] = mtime
        except (OSError, ValueError, TypeError):
            log.warning("revoked_tokens.json cannot be read: keeping the previous list")
    return st["keys"]


def revoke_token(claims: dict) -> None:
    """Adds the token to the revocation list until its natural expiry."""
    now = int(time.time())
    with _revoked_lock:
        current = {k: v for k, v in _revoked_keys().items() if v > now}
        current[revocation_key(claims)] = int(claims.get("exp", now))
        from api.safefile import write_text_atomic
        _REVOKED_FILE.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(_REVOKED_FILE, json.dumps(current))


def prune_revoked() -> int:
    """Drops from the revocation list the tokens that have expired anyway (nothing else
    cleans it between logouts). Returns how many were dropped."""
    now = int(time.time())
    with _revoked_lock:
        keys = _revoked_keys()
        current = {k: v for k, v in keys.items() if v > now}
        dropped = len(keys) - len(current)
        if dropped:
            from api.safefile import write_text_atomic
            write_text_atomic(_REVOKED_FILE, json.dumps(current))
        return dropped


# ---------------------------------------------------------------------------
# User store (data/users.json)
# ---------------------------------------------------------------------------
def _read_users() -> list[dict] | None:
    """The user list, or None if the file is missing or cannot be read."""
    try:
        data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, list) else None


def _load_users() -> list[dict]:
    return _read_users() or []


_users_state: dict = {"mtime": None, "users": None}


def _users_cached() -> list[dict] | None:
    """The user list, reread only when users.json changes (checked on every request).
    None if the file is missing, unreadable or invalid: nobody is let in."""
    try:
        mtime = os.stat(USERS_FILE).st_mtime
    except OSError:
        return None
    if _users_state["mtime"] != mtime:
        users = _read_users()
        if users is None:
            # fail closed: sessions are refused until the file is valid again (writes are
            # atomic, so a half-written file is never seen; an invalid one is an incident)
            log.error("users.json is unreadable or invalid: every session is refused until it is fixed")
            _users_state["mtime"], _users_state["users"] = None, None
            return None
        _users_state["mtime"], _users_state["users"] = mtime, users
    return _users_state["users"]


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


def write_users(users: list[dict]) -> None:
    """Writes users.json atomically (random temporary name, fsync, rename), keeping
    owner and permissions. The caller holds file_lock(USERS_FILE) for the whole
    read-modify-write."""
    from api.safefile import write_text_atomic
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(USERS_FILE, json.dumps(users, ensure_ascii=False, indent=2))


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
    locked = account_guard.login_locked(req.email)
    # a locked account is checked against the dummy hash: same time, same answer as a wrong password
    stored = user["pw_hash"] if user and not locked else _DUMMY_HASH
    async with _password_slots:   # a few at a time: scrypt is deliberately heavy
        ok = await asyncio.to_thread(verify_password, req.password, stored)
    if not user or not ok or locked:
        if not locked:
            account_guard.login_failed(req.email)
        raise HTTPException(status_code=401, detail="Email o password non validi")
    account_guard.login_ok(req.email)
    if needs_rehash(user["pw_hash"]):
        await asyncio.to_thread(_upgrade_hash, user["id"], req.password, user["pw_hash"])

    token = create_token(user["id"], user.get("name", user["email"]), int(user.get("auth_version", 0)))
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


def create_reset_token(user: dict, minutes: int = _RESET_TTL_MINUTES) -> str:
    """Token of the password reset link. It carries a fingerprint of the current
    password hash, so it stops working as soon as the password changes (one use)."""
    payload = json.dumps(
        {"uid": user["id"], "f": _fingerprint(user),
         "exp": int(time.time()) + minutes * 60},
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


def reset_link(token: str) -> str | None:
    """Absolute link to the reset page, on the address the administrator configured
    (PUBLIC_BASE_URL, or the origin of PILOT_LOGIN_URL). Never built from the Host
    header of the request, which a caller can choose: None if nothing is configured."""
    base = PUBLIC_BASE_URL.rstrip("/")
    if not base and PILOT_LOGIN_URL:
        parts = urlsplit(PILOT_LOGIN_URL)
        if parts.scheme in ("http", "https") and parts.netloc:
            base = f"{parts.scheme}://{parts.netloc}"
    if not base:
        return None
    return f"{base}/auth/reset#token={quote(token, safe='')}"


def _send_reset_email(user: dict) -> None:
    """Sends the reset link (in the background, so that the reply to the request
    takes the same time whether or not the address is registered)."""
    try:
        from api.mailer import send_password_reset_link, smtp_configured
        if not smtp_configured():
            return
        link = reset_link(create_reset_token(user))
        if link is None:
            log.warning("Password reset link not sent: set PUBLIC_BASE_URL (or PILOT_LOGIN_URL)")
            return
        send_password_reset_link(user["email"], user.get("name", ""), link, _RESET_TTL_MINUTES)
    except Exception:
        log.warning("Password reset email not sent to %s", user.get("email"), exc_info=True)


@router.post("/forgot")
@limiter.limit("5/hour")
async def forgot(request: Request, req: ForgotRequest, background_tasks: BackgroundTasks):
    """Self-service password reset, step 1: emails a single-use link.

    Nothing changes until the link is used (otherwise anybody who knows an address
    could lock that person out). The response is always identical, and sent before
    the mail is, whether or not the address exists (anti-enumeration).
    """
    generic = {
        "ok": True,
        "message": "Se l'indirizzo è registrato, riceverai un'email con il link per scegliere una nuova password.",
    }
    if not AUTH_ENABLED:
        raise HTTPException(status_code=400, detail="Autenticazione non attiva")

    user = _find_user_by_email(req.email)
    if user and account_guard.reset_email_allowed(req.email):
        background_tasks.add_task(_send_reset_email, user)
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
// the token travels in the fragment, which browsers never send to servers or proxies
// (links sent before 0.7.1 carried it in the query string: still accepted)
const T = new URLSearchParams(location.hash.slice(1)).get('token')
  || new URLSearchParams(location.search).get('token') || '';
history.replaceState(null, '', location.pathname);
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
    page = _PAGE.format(body=body)
    return HTMLResponse(page, status_code=status,
                        headers={"Referrer-Policy": "no-referrer", "Cache-Control": "no-store",
                                 **csp.headers_for(page)})


@router.get("/reset", response_class=HTMLResponse)
async def reset_page():
    """Password reset, step 2: the page where the new password is chosen. The token is in
    the fragment of the address (read by the page, checked when the password is sent)."""
    if not AUTH_ENABLED:
        return _html('<p class="err">Il link non è valido o è scaduto.</p>', 400)
    login = (f'<p id="go" hidden><a href="{html.escape(PILOT_LOGIN_URL, quote=True)}">Vai all\'accesso</a></p>'
             if PILOT_LOGIN_URL.startswith(("http://", "https://")) else "")
    return _html(_FORM.format(min=_MIN_PASSWORD, login=login))


class ResetRequest(BaseModel):
    token: str = Field(..., max_length=2000)
    password: str = Field(..., max_length=200)


def consume_reset_token(token: str, new_password: str) -> bool | None:
    """Sets the password for a valid reset token. The token is checked again inside the
    lock of users.json, right before the write: two requests with the same token cannot
    both succeed. None = token not valid (any more); False = could not save."""
    new_hash = hash_password(new_password)   # slow: done before taking the lock
    with file_lock(USERS_FILE):
        user = _user_from_reset_token(token)
        if user is None:
            return None
        users = _load_users()
        for u in users:
            if u.get("id") == user["id"]:
                u["pw_hash"] = new_hash
                u["auth_version"] = int(u.get("auth_version", 0)) + 1
        try:
            write_users(users)
        except (OSError, TypeError, ValueError):
            return False
        return True


@router.post("/reset")
@limiter.limit("10/hour")
async def reset_password(request: Request, req: ResetRequest):
    """Password reset, step 3: sets the chosen password; the link stops working and
    the older sessions of the user end."""
    if not AUTH_ENABLED:
        raise HTTPException(status_code=400, detail="Autenticazione non attiva")
    if _user_from_reset_token(req.token) is None:
        raise HTTPException(status_code=400, detail="Il link non è valido o è scaduto.")
    if len(req.password) < _MIN_PASSWORD:
        raise HTTPException(status_code=400,
                            detail=f"La password deve avere almeno {_MIN_PASSWORD} caratteri.")
    async with _password_slots:
        done = await asyncio.to_thread(consume_reset_token, req.token, req.password)
    if done is None:
        raise HTTPException(status_code=400, detail="Il link non è valido o è scaduto.")
    if not done:
        raise HTTPException(status_code=500, detail="Impossibile salvare la password.")
    return {"ok": True}
