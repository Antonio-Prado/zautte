"""
Applications of citizens who ask to take part in the pilot (APPLY_ENABLED).

`POST /apply` (public, rate-limited) stores one JSONL row per application in
data/applications.jsonl: id, time, name, email, age, district, a line about how the person uses
the services, the two declarations (of age, notice read) and a status. The administrator
reads them (`GET /applications`, or `scripts/applications.py`) and accepts one
(`POST /applications/accept`): an account is created and the invitation with the link to
choose the password is sent, like `adduser --send-email`. Rows expire after
RETENTION_APPLICATIONS_DAYS (purge_logs).
"""

from __future__ import annotations

import logging
import re
import secrets

from pydantic import BaseModel, Field

from api.auth import (
    USERS_FILE,
    _load_users,
    create_reset_token,
    hash_password,
    reset_link,
    write_users,
)
from api.datalog import APPLICATIONS_FILE, _read_jsonl, rewrite_jsonl
from api.filelock import file_lock
from api.safefile import dump_line
from config.settings import now_local

log = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[\w.+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63}){0,8}\.[A-Za-z]{2,24}$")
_INVITE_HOURS = 48


def _one_line(text: str, limit: int) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()[:limit]


class ApplyRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=120, description="nome e cognome")
    email: str = Field(..., max_length=200)
    age: int = Field(..., ge=18, le=120, description="età in anni")
    district: str = Field("", max_length=120, description="quartiere di residenza o domicilio")
    usage: str = Field("", max_length=500, description="come usa di solito i servizi")
    adult: bool = Field(..., description="dichiara di avere almeno 18 anni")
    notice_read: bool = Field(..., description="dichiara di aver letto l'informativa")
    website: str = Field("", max_length=200, description="honeypot: must stay empty")


def validate(req: ApplyRequest) -> str | None:
    """The reason the application is refused, or None."""
    if req.website:
        return "spam"   # a field people do not see, filled by a robot
    if not (req.adult and req.notice_read):
        return "Entrambe le dichiarazioni sono necessarie"
    if not _EMAIL_RE.match(req.email.strip()):
        return "Indirizzo email non valido"
    if not _one_line(req.name, 120):
        return "Nome mancante"
    return None


def store(req: ApplyRequest) -> dict:
    """Appends the application and returns it (with its id)."""
    entry = {
        "id": secrets.token_hex(6),
        "ts": now_local().isoformat(timespec="seconds"),
        "name": _one_line(req.name, 120),
        "email": req.email.strip().lower(),
        "age": req.age,
        "district": _one_line(req.district, 120),
        "usage": _one_line(req.usage, 500),
        "adult": True,
        "notice_read": True,
        "status": "new",
    }
    APPLICATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(APPLICATIONS_FILE), open(APPLICATIONS_FILE, "a", encoding="utf-8") as f:
        f.write(dump_line(entry) + "\n")
    return entry


def list_all() -> list[dict]:
    return _read_jsonl(APPLICATIONS_FILE)


def _set_status(aid: str, status: str, **extra) -> dict | None:
    found: dict | None = None

    def transform(e: dict) -> dict | None:
        nonlocal found
        if e.get("id") == aid:
            e = {**e, "status": status, **extra}
            found = e
        return e

    rewrite_jsonl(APPLICATIONS_FILE, transform, dry_run=False)
    return found


def accept(aid: str) -> tuple[dict, str]:
    """Creates the account for the application `aid` and returns (user, invitation link).
    Raises ValueError if the application is unknown, already accepted, or the email already has an
    account. The caller sends the invitation (api.mailer.send_invitation) and marks the row."""
    app = next((a for a in list_all() if a.get("id") == aid), None)
    if app is None:
        raise ValueError("Candidatura non trovata")
    if app.get("status") == "accepted":
        raise ValueError("Candidatura già accettata")
    email = app["email"]
    with file_lock(USERS_FILE):
        users = _load_users()
        if any(u.get("email", "").strip().lower() == email for u in users):
            raise ValueError("Esiste già un account con questa email")
        user = {
            "id": secrets.token_hex(8),
            "name": app["name"],
            "email": email,
            "pw_hash": hash_password(secrets.token_urlsafe(24)),   # unknown to everybody: set through the link
            "created": now_local().date().isoformat(),
        }
        users.append(user)
        write_users(users)
    link = reset_link(create_reset_token(user, minutes=_INVITE_HOURS * 60))
    if link is None:
        raise ValueError("PUBLIC_BASE_URL (o PILOT_LOGIN_URL) non impostato: impossibile costruire il link")
    return user, link


def mark(aid: str, status: str) -> dict | None:
    """Sets the status of an application (accepted, rejected, new)."""
    return _set_status(aid, status, **{f"{status}_ts": now_local().isoformat(timespec="seconds")})


INVITE_HOURS = _INVITE_HOURS
