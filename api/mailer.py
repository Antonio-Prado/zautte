"""
Email sending (standard library smtplib).

Used to deliver the access credentials to the pilot participants.
Config via .env / config.settings:
    SMTP_HOST, SMTP_PORT, SMTP_FROM   (required to send)
    SMTP_USER, SMTP_PASSWORD          (optional: only if the relay requires auth)
    SMTP_STARTTLS                     (optional: true for STARTTLS, port 587)
    SMTP_SSL                          (optional: true for implicit TLS, port 465)

Organisation's internal relay (recipients in the organisation's domain):
    SMTP_HOST=mail.example.it  SMTP_PORT=25  (no auth, no TLS)
Authenticated SMTP (e.g. Carbonio):
    SMTP_HOST=<host> SMTP_PORT=465 SMTP_SSL=true SMTP_USER=... SMTP_PASSWORD=...
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from config.settings import (
    SMTP_FROM,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_SSL,
    SMTP_STARTTLS,
    SMTP_USER,
)


def smtp_configured() -> bool:
    return bool(SMTP_HOST and SMTP_FROM)


def send_email(to: str, subject: str, body: str, reply_to: str = "") -> None:
    """Sends a plain-text email. Raises an exception if sending fails.
    `reply_to`: address where replies go (the sender stays SMTP_FROM)."""
    if not smtp_configured():
        raise RuntimeError("SMTP non configurato (SMTP_HOST/SMTP_FROM mancanti)")

    msg = EmailMessage()
    msg["From"] = formataddr(("Zautte", SMTP_FROM))
    msg["To"] = to
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body)

    if SMTP_SSL:
        smtp = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=20,
                                context=ssl.create_default_context())
    else:
        smtp = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20)
    with smtp as s:
        s.ehlo()
        if SMTP_STARTTLS and not SMTP_SSL:
            s.starttls(context=ssl.create_default_context())
            s.ehlo()
        if SMTP_USER:
            s.login(SMTP_USER, SMTP_PASSWORD)
        s.send_message(msg)


def send_credentials(to: str, name: str, password: str, login_url: str) -> None:
    """Delivers the pilot access credentials to a participant."""
    subject = "Accesso a Zautte — assistente virtuale (pilota)"
    link = f"\nAccedi qui: {login_url}\n" if login_url else ""
    body = f"""Ciao {name},

sei stato abilitato a testare Zautte, l'assistente virtuale.
{link}
Le tue credenziali:
  Email:    {to}
  Password: {password}

Ti chiediamo di non condividere queste credenziali con altri.
Per qualsiasi problema puoi rispondere a questa email.

Grazie per la collaborazione.
"""
    send_email(to, subject, body)


def build_feedback_notification(entry: dict, dashboard_url: str = "") -> tuple[str, str]:
    """Subject and body of the email for a report (👎 with comment/link).
    `entry` is the row of data/feedback.jsonl."""
    user = entry.get("user") or "un collega"
    question = (entry.get("question") or "").strip()
    subject = f"Zautte — segnalazione di {user}: {question[:70]}"
    when = (entry.get("details_ts") or entry.get("ts") or "").replace("T", " ")[:19]
    urls = entry.get("urls") or []
    lines = [
        f"{user} ha segnalato una risposta non soddisfacente ({when}).",
        "",
        "Domanda:",
        f"  {question}",
        "",
        "Risposta (inizio):",
        f"  {(entry.get('answer_preview') or '').strip()}",
        "",
        "Commento:",
        f"  {(entry.get('comment') or '').strip() or '(nessuno)'}",
        "",
        "Pagine con l'informazione corretta:",
    ]
    lines += [f"  {u}" for u in urls] if urls else ["  (nessuna)"]
    if dashboard_url:
        lines += ["", f"Dashboard (vista amministratore): {dashboard_url}"]
    lines += ["", "Email automatica di Zautte: la segnalazione è già nella dashboard,",
              "dove può essere marcata come risolta."]
    return subject, "\n".join(lines) + "\n"


def send_feedback_notification(to: str, entry: dict, dashboard_url: str = "") -> None:
    """Notifies the administrator of a new report."""
    subject, body = build_feedback_notification(entry, dashboard_url)
    send_email(to, subject, body)


def build_feedback_resolved(entry: dict, name: str = "", note: str = "",
                            login_url: str = "") -> tuple[str, str]:
    """Subject and body of the acknowledgement email to the colleague who reported,
    when the administrator marks the feedback as resolved."""
    question = (entry.get("question") or "").strip()
    subject = f"Zautte — la tua segnalazione è stata risolta: {question[:70]}"
    lines = [
        f"Ciao {name or 'collega'},",
        "",
        "grazie per la segnalazione su Zautte: l'abbiamo esaminata e risolta.",
        "",
        "Domanda segnalata:",
        f"  {question}",
    ]
    if entry.get("comment"):
        lines += ["", "Il tuo commento:", f"  {entry['comment'].strip()}"]
    if entry.get("urls"):
        lines += ["", "Pagine che avevi indicato:"] + [f"  {u}" for u in entry["urls"]]
    if note:
        lines += ["", "Nota di chi ha risolto:", f"  {note.strip()}"]
    lines += ["", "Puoi verificare ponendo di nuovo la domanda all'assistente"
              + (f": {login_url}" if login_url else "."),
              "Se la risposta non ti convince ancora, rispondi a questa email oppure usa di nuovo",
              "il 👎 con il modulo di segnalazione.",
              "",
              "Grazie per la collaborazione."]
    return subject, "\n".join(lines) + "\n"


def send_feedback_resolved(to: str, name: str, entry: dict, note: str = "",
                           login_url: str = "", reply_to: str = "") -> None:
    """Acknowledgement email to the colleague: their report has been resolved."""
    subject, body = build_feedback_resolved(entry, name, note, login_url)
    send_email(to, subject, body, reply_to=reply_to)


def send_password_reset(to: str, name: str, password: str, login_url: str) -> None:
    """Delivers a new password after a reset request."""
    subject = "Zautte — nuova password"
    link = f"\nAccedi qui: {login_url}\n" if login_url else ""
    body = f"""Ciao {name},

hai richiesto il reset della password per Zautte. Ecco la nuova password:
{link}
  Email:    {to}
  Password: {password}

Puoi accedere subito con questa password.
Se NON hai richiesto tu il reset, avvisa l'amministratore.
"""
    send_email(to, subject, body)
