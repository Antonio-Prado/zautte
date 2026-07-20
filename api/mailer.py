"""
Invio email (standard library smtplib).

Usato per recapitare le credenziali di accesso ai partecipanti del pilota.
Config via .env / config.settings:
    SMTP_HOST, SMTP_PORT, SMTP_FROM   (obbligatori per inviare)
    SMTP_USER, SMTP_PASSWORD          (opzionali: solo se il relay richiede auth)
    SMTP_STARTTLS                     (opzionale: true per STARTTLS)

Relay interno del Comune (destinatari @comunesbt.it):
    SMTP_HOST=mail.comunesbt.it  SMTP_PORT=25  (niente auth, niente TLS)
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
    SMTP_STARTTLS,
    SMTP_USER,
)


def smtp_configured() -> bool:
    return bool(SMTP_HOST and SMTP_FROM)


def send_email(to: str, subject: str, body: str) -> None:
    """Invia un'email di testo. Solleva un'eccezione se l'invio fallisce."""
    if not smtp_configured():
        raise RuntimeError("SMTP non configurato (SMTP_HOST/SMTP_FROM mancanti)")

    msg = EmailMessage()
    msg["From"] = formataddr(("Zautte", SMTP_FROM))
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as s:
        s.ehlo()
        if SMTP_STARTTLS:
            s.starttls(context=ssl.create_default_context())
            s.ehlo()
        if SMTP_USER:
            s.login(SMTP_USER, SMTP_PASSWORD)
        s.send_message(msg)


def send_credentials(to: str, name: str, password: str, login_url: str) -> None:
    """Recapita a un partecipante le credenziali di accesso al pilota."""
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
