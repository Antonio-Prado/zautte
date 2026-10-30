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
    DAILY_QUESTION_LIMIT,
    NEWS_CHANNEL_NAME,
    NEWS_CHANNEL_URL,
    PILOT_PERIOD,
    SITE_NAME,
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
    msg["Subject"] = " ".join(subject.split())[:200]   # a line break in a question must not break the header
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body)

    if SMTP_USER and not (SMTP_SSL or SMTP_STARTTLS) and SMTP_HOST not in ("localhost", "127.0.0.1", "::1"):
        # never send the relay password in clear text over the network
        raise RuntimeError("SMTP_USER requires SMTP_SSL or SMTP_STARTTLS (the password would travel unencrypted)")

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


def send_invitation(to: str, name: str, link: str, hours: int) -> None:
    """Invites a participant: a single-use link to choose the password (no password is
    ever sent by email)."""
    subject = "Accesso a Zautte — assistente virtuale (pilota)"
    body = f"""Ciao {name},

ti diamo il benvenuto nella sperimentazione di Zautte, l'assistente virtuale. Per iniziare scegli la tua password da questo link:

  {link}

Il link vale {hours} ore e si può usare una sola volta; se scade, dal pulsante «Password dimenticata?» della pagina di accesso ne ottieni uno nuovo.
Ti chiediamo di non condividere le tue credenziali con altri.

{pilot_request_text()}
Per qualsiasi problema puoi rispondere a questa email.
{news_channel_text()}
Grazie per la collaborazione.
"""
    send_email(to, subject, body)


def news_channel_text() -> str:
    """The closing paragraph of every email to a participant, inviting to follow the
    organisation's news channel (NEWS_CHANNEL_URL, NEWS_CHANNEL_NAME). Empty when there is no
    channel. Starts with a blank line so that it can follow any paragraph."""
    if not NEWS_CHANNEL_URL:
        return ""
    if SITE_NAME.lower().startswith("comune "):
        whose = f" del {SITE_NAME}"   # "del Comune di …"
    else:
        whose = f" di {SITE_NAME}" if SITE_NAME else ""
    return f"\nResta aggiornato sulle notizie{whose}: iscriviti al {NEWS_CHANNEL_NAME}\n  {NEWS_CHANNEL_URL}\n"


def pilot_request_text() -> str:
    """The paragraph "what we ask of you" shared by the invitation and the welcome emails:
    questions, thumbs up/down, comment and link after a thumbs down, daily limit
    (DAILY_QUESTION_LIMIT, omitted when 0) and period of the pilot (PILOT_PERIOD, omitted when empty)."""
    lines = [
        "Cosa ti chiediamo:",
        "- fai a Zautte le domande che faresti allo sportello o al telefono, con parole tue;",
        "- valuta ogni risposta con il pollice in su 👍 o in giù 👎, sotto la risposta;",
        "- se metti 👎, nel modulo che compare scrivi cosa non va e, se puoi, incolla il link della pagina giusta.",
    ]
    tail = []
    if DAILY_QUESTION_LIMIT > 0:
        tail.append(f"Puoi fare fino a {DAILY_QUESTION_LIMIT} domande al giorno")
    if PILOT_PERIOD:
        tail.append(f"la sperimentazione dura {PILOT_PERIOD}" if tail else f"La sperimentazione dura {PILOT_PERIOD}")
    if tail:
        lines.append("; ".join(tail) + ".")
    return "\n".join(lines) + "\n"


def build_feedback_notification(entry: dict, dashboard_url: str = "") -> tuple[str, str]:
    """Subject and body of the email for a report (👎 with comment/link).
    `entry` is the row of data/feedback.jsonl."""
    user = entry.get("user") or "un collega"
    question = (entry.get("question") or "").strip()
    when = (entry.get("details_ts") or entry.get("ts") or "").replace("T", " ")[:19]
    subject = f"Zautte — nuova segnalazione ({when[:10]})"   # no question or name: subjects end up in mail logs
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
        "Pagine con l'informazione corretta, indicate dal collega (link non verificati):",
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
    subject = "Zautte — la tua segnalazione è stata risolta"
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
    # No invitation to ask the question again: every repeated question costs tokens
    # (and the colleague has already read the note); the way back is the email or the 👎.
    lines += ["", "Se qualcosa non ti convince ancora, rispondi a questa email oppure usa di nuovo",
              "il 👎 con il modulo di segnalazione."]
    if news_channel_text():
        lines += [news_channel_text().rstrip("\n")]
    lines += ["", "Grazie per la collaborazione."]
    return subject, "\n".join(lines) + "\n"


def send_feedback_resolved(to: str, name: str, entry: dict, note: str = "",
                           login_url: str = "", reply_to: str = "") -> None:
    """Acknowledgement email to the colleague: their report has been resolved."""
    subject, body = build_feedback_resolved(entry, name, note, login_url)
    send_email(to, subject, body, reply_to=reply_to)


def send_password_reset_link(to: str, name: str, link: str, minutes: int) -> None:
    """Sends the link to choose a new password (nothing changes until it is used)."""
    subject = "Zautte — scegli una nuova password"
    body = f"""Ciao {name},

hai chiesto di reimpostare la password di Zautte. Per sceglierne una nuova apri questo link:

  {link}

Il link vale {minutes} minuti e si può usare una sola volta. Finché non lo usi, la tua password attuale resta valida.
Se NON hai fatto tu la richiesta, ignora questa email: non succede nulla.
{news_channel_text()}"""
    send_email(to, subject, body)
