"""
Autenticazione utenti di Zautte.

Login email + password per un gruppo ristretto (progetto pilota). Il token di
sessione è STATELESS e firmato con HMAC-SHA256: qualsiasi worker uvicorn lo
valida senza stato condiviso (in produzione girano due processi, IPv4 e IPv6,
quindi un session-store su file darebbe race condition e login "persi").

Nessuna dipendenza esterna: solo standard library.
Gli utenti sono provisioned in data/users.json (vedi scripts/adduser.py):
    [{"id": "...", "name": "Mario Rossi", "email": "...", "pw_hash": "scrypt$..."}]
La password non è mai salvata in chiaro: solo l'hash scrypt.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from fastapi import APIRouter, HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from api.limiter import limiter
from config.settings import (
    AUTH_ENABLED,
    AUTH_SECRET,
    AUTH_TOKEN_TTL_DAYS,
    USERS_FILE,
)

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Password hashing (scrypt, standard library)
# ---------------------------------------------------------------------------
_SCRYPT_N = 2 ** 14      # ~16 MB di memoria: sotto il maxmem di default (32 MB)
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32


def hash_password(password: str) -> str:
    """Ritorna una stringa auto-descrittiva 'scrypt$N$r$p$salt$hash' (base64)."""
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
    """Confronto in tempo costante contro un hash prodotto da hash_password()."""
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
    except Exception:
        return False


# Hash "esca" calcolato una volta all'import: verificando sempre la password
# (anche quando l'email non esiste) i tempi di risposta non rivelano quali
# email sono registrate.
_DUMMY_HASH = hash_password(secrets.token_hex(16))


# ---------------------------------------------------------------------------
# Token di sessione firmato (HMAC-SHA256, formato compatto tipo JWT)
# ---------------------------------------------------------------------------
def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(msg: str) -> str:
    sig = hmac.new(AUTH_SECRET.encode("utf-8"), msg.encode("ascii"), hashlib.sha256).digest()
    return _b64url_encode(sig)


def create_token(uid: str, name: str) -> str:
    exp = int(time.time()) + AUTH_TOKEN_TTL_DAYS * 86400
    payload = json.dumps(
        {"uid": uid, "name": name, "exp": exp},
        separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    msg = _b64url_encode(payload)
    return f"{msg}.{_sign(msg)}"


def verify_token(token: str) -> dict | None:
    """Ritorna i claim se firma valida e non scaduto, altrimenti None."""
    try:
        msg, sig = token.split(".", 1)
        if not hmac.compare_digest(sig, _sign(msg)):
            return None
        claims = json.loads(_b64url_decode(msg))
        if int(claims.get("exp", 0)) < int(time.time()):
            return None
        return claims
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Store utenti (data/users.json)
# ---------------------------------------------------------------------------
def _load_users() -> list[dict]:
    if not USERS_FILE.exists():
        return []
    try:
        data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _find_user_by_email(email: str) -> dict | None:
    email = email.strip().lower()
    for u in _load_users():
        if u.get("email", "").strip().lower() == email:
            return u
    return None


# ---------------------------------------------------------------------------
# Dependency: identità dell'utente della richiesta
# ---------------------------------------------------------------------------
_bearer = HTTPBearer(auto_error=False)


async def require_user(
    cred: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> dict:
    """
    Dependency per proteggere gli endpoint utente.
    - AUTH_ENABLED=false → autenticazione disattivata: identità 'anon'.
    - AUTH_ENABLED=true  → richiede un token 'Authorization: Bearer <token>' valido.
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


@router.get("/me")
async def me(user: dict = Security(require_user)):
    """Restituisce l'identità dell'utente autenticato."""
    return {"uid": user.get("uid"), "name": user.get("name")}
