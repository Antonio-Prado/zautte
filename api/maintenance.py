"""
Maintenance mode: entered on its own when the model provider refuses to serve (API
credit exhausted, key not valid or without permission, billing problem).

While it lasts (MAINTENANCE_HOLD_SECONDS, then the model is tried again) the chat answers
HTTP 503 with a neutral text and never calls the model; the public /health only says
`maintenance: true`. The real cause goes to the log, to the admin view of /health and to
the alert e-mail (MAINTENANCE_ALERT_EMAIL, at most one an hour), plus one e-mail when the
service is back. Nothing about the cause reaches the visitors.
"""

from __future__ import annotations

import datetime
import logging
import threading
import time

from config.settings import MAINTENANCE_ALERT_EMAIL, MAINTENANCE_HOLD_SECONDS, SITE_NAME

log = logging.getLogger("maintenance")

MESSAGE_IT = "Il servizio è temporaneamente in manutenzione. Riprova tra qualche minuto."
MESSAGE_EN = "The service is temporarily under maintenance. Please try again in a few minutes."

_ALERT_EVERY_SECONDS = 3600

_lock = threading.Lock()
_until = 0.0                  # the chat refuses the model until this time
_since: float | None = None   # start of the incident (None = no incident)
_reason = ""
_last_alert = 0.0


def classify(exc: BaseException) -> str | None:
    """The reason of the outage when the exception means that the provider refuses to
    serve at all (not a transient error of one request); None otherwise."""
    try:
        import anthropic
    except ImportError:
        return None
    msg = str(getattr(exc, "message", "") or exc)
    low = msg.lower()
    if isinstance(exc, anthropic.AuthenticationError):
        return f"API key not valid (authentication error): {msg[:200]}"
    if isinstance(exc, anthropic.PermissionDeniedError):
        return f"API key without permission (permission denied): {msg[:200]}"
    if isinstance(exc, anthropic.BadRequestError) and any(
            w in low for w in ("credit", "balance", "billing")):
        return f"API credit exhausted: {msg[:200]}"
    if isinstance(exc, anthropic.APIStatusError) and getattr(exc, "status_code", 0) == 402:
        return f"payment required (HTTP 402): {msg[:200]}"
    return None


def active() -> bool:
    """True while the chat must answer "maintenance" instead of calling the model."""
    return time.time() < _until


def activate(reason: str) -> None:
    """Starts (or renews) the maintenance mode; alerts the administrator at most once
    an hour."""
    global _until, _since, _reason, _last_alert
    now = time.time()
    with _lock:
        first = _since is None
        if first:
            _since = now
        _until = now + MAINTENANCE_HOLD_SECONDS
        _reason = reason
        alert = now - _last_alert >= _ALERT_EVERY_SECONDS
        if alert:
            _last_alert = now
    log.error("Maintenance mode %s (model provider unavailable): %s",
              "started" if first else "renewed", reason)
    if alert:
        _notify("Zautte — servizio in manutenzione",
                f"Zautte{_site()} ha smesso di rispondere alle domande: il fornitore del modello "
                f"rifiuta le richieste.\n\nCausa: {reason}\n\n"
                f"Dalle: {_fmt(_since)}\n\n"
                f"Finché dura, chi scrive vede «{MESSAGE_IT}», senza altre spiegazioni; "
                f"ogni {MAINTENANCE_HOLD_SECONDS // 60} minuti il modello viene riprovato. "
                "Questa email si ripete al massimo una volta all'ora; un'altra arriva quando il "
                "servizio riprende.\n")


def recovered() -> None:
    """Called after a successful model call: closes the incident, if there was one."""
    global _until, _since, _reason, _last_alert
    if _since is None:
        return
    with _lock:
        if _since is None:
            return
        since, reason = _since, _reason
        _until, _since, _reason, _last_alert = 0.0, None, "", 0.0
    log.warning("Maintenance mode ended: the model answers again (was: %s)", reason)
    _notify("Zautte — servizio di nuovo attivo",
            f"Zautte{_site()} risponde di nuovo alle domande.\n\n"
            f"Manutenzione dalle {_fmt(since)} alle {_fmt(time.time())}.\nCausa: {reason}\n")


def status() -> dict:
    """For the administrator: whether the mode is active, since when and why."""
    return {"active": active(), "since": _fmt(_since) if _since else None,
            "reason": _reason or None}


def _site() -> str:
    return f" ({SITE_NAME})" if SITE_NAME else ""


def _fmt(ts: float | None) -> str:
    if not ts:
        return "-"
    return datetime.datetime.fromtimestamp(ts).astimezone().strftime("%d/%m/%Y %H:%M")


def _notify(subject: str, body: str) -> None:
    """Alert e-mail in a background thread: never slows down or breaks a request."""
    if not MAINTENANCE_ALERT_EMAIL:
        return

    def _send() -> None:
        try:
            from api.mailer import send_email, smtp_configured
            if smtp_configured():
                send_email(MAINTENANCE_ALERT_EMAIL, subject, body)
        except Exception:
            log.warning("Maintenance alert not sent to %s", MAINTENANCE_ALERT_EMAIL, exc_info=True)

    threading.Thread(target=_send, name="maintenance-alert", daemon=True).start()
