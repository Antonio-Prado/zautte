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
# until: the chat refuses the model until this time; since: start of the incident (None =
# no incident); reason: fixed description of the cause (never the provider's message, which
# goes to the log only); last_alert: time of the last alert e-mail.
_state: dict = {"until": 0.0, "since": None, "reason": "", "last_alert": 0.0}


def classify(exc: BaseException) -> str | None:
    """A fixed description of the outage when the exception means that the provider refuses
    to serve at all (not a transient error of one request); None otherwise. The provider's
    own message is never returned: it is logged by activate()."""
    try:
        import anthropic
    except ImportError:
        return None
    low = str(getattr(exc, "message", "") or exc).lower()
    if isinstance(exc, anthropic.AuthenticationError):
        return "API key not valid (authentication error)"
    if isinstance(exc, anthropic.PermissionDeniedError):
        return "API key without permission (permission denied)"
    if isinstance(exc, anthropic.BadRequestError) and any(
            w in low for w in ("credit", "balance", "billing")):
        return "API credit exhausted"
    if isinstance(exc, anthropic.APIStatusError) and getattr(exc, "status_code", 0) == 402:
        return "payment required (HTTP 402)"
    return None


def active() -> bool:
    """True while the chat must answer "maintenance" instead of calling the model."""
    return time.time() < _state["until"]


def activate(reason: str, exc: BaseException | None = None) -> None:
    """Starts (or renews) the maintenance mode; alerts the administrator at most once
    an hour. `exc` (the provider's error) is written to the log only."""
    now = time.time()
    with _lock:
        first = _state["since"] is None
        if first:
            _state["since"] = now
        _state["until"] = now + MAINTENANCE_HOLD_SECONDS
        _state["reason"] = reason
        alert = now - _state["last_alert"] >= _ALERT_EVERY_SECONDS
        if alert:
            _state["last_alert"] = now
        since = _state["since"]
    log.error("Maintenance mode %s (model provider unavailable): %s%s",
              "started" if first else "renewed", reason,
              f" — {str(exc)[:300]}" if exc is not None else "")
    if alert:
        _notify("Zautte — servizio in manutenzione",
                f"Zautte{_site()} ha smesso di rispondere alle domande: il fornitore del modello "
                f"rifiuta le richieste.\n\nCausa: {reason} (il messaggio completo è nel log del server)\n\n"
                f"Dalle: {_fmt(since)}\n\n"
                f"Finché dura, chi scrive vede «{MESSAGE_IT}», senza altre spiegazioni; "
                f"ogni {MAINTENANCE_HOLD_SECONDS // 60} minuti il modello viene riprovato. "
                "Questa email si ripete al massimo una volta all'ora; un'altra arriva quando il "
                "servizio riprende.\n")


def recovered() -> None:
    """Called after a successful model call: closes the incident, if there was one."""
    if _state["since"] is None:
        return
    with _lock:
        if _state["since"] is None:
            return
        since, reason = _state["since"], _state["reason"]
        _state.update(until=0.0, since=None, reason="", last_alert=0.0)
    log.warning("Maintenance mode ended: the model answers again (was: %s)", reason)
    _notify("Zautte — servizio di nuovo attivo",
            f"Zautte{_site()} risponde di nuovo alle domande.\n\n"
            f"Manutenzione dalle {_fmt(since)} alle {_fmt(time.time())}.\nCausa: {reason}\n")


def status() -> dict:
    """For the administrator: whether the mode is active, since when and why."""
    return {"active": active(), "since": _fmt(_state["since"]) if _state["since"] else None,
            "reason": _state["reason"] or None}


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
