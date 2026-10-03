"""
Mascheramento dei dati personali riconoscibili nel testo delle domande.

La domanda del visitatore arriva al modello linguistico (fornitore esterno),
ai log locali (usage.jsonl, gaps.jsonl, feedback.jsonl, stats.json) e al log
dell'applicazione. Il widget chiede di non inserire dati personali ma non può
impedirlo: qui i dati con un formato riconoscibile vengono sostituiti da un
segnaposto PRIMA di qualsiasi uso.

Mascherati: codice fiscale, IBAN (con verifica del codice di controllo), numeri
di carta di pagamento (con verifica di Luhn), indirizzi email (tranne quelli dei
domini istituzionali in PII_KEEP_EMAIL_DOMAINS) e numeri di telefono italiani.
Nomi, indirizzi di residenza e altri dati in forma libera non sono riconoscibili
in modo affidabile e restano: l'avviso nel widget resta necessario.
"""

import re

from config.settings import PII_KEEP_EMAIL_DOMAINS, PII_REDACTION

# Codice fiscale delle persone fisiche, anche in omocodia (cifre sostituite
# dalle lettere LMNPQRSTUV).
_CF_RE = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z]{6}[0-9LMNPQRSTUVlmnpqrstuv]{2}[ABCDEHLMPRSTabcdehlmprst]"
    r"[0-9LMNPQRSTUVlmnpqrstuv]{2}[A-Za-z][0-9LMNPQRSTUVlmnpqrstuv]{3}[A-Za-z](?![A-Za-z0-9])"
)

# IBAN: paese + 2 cifre di controllo + 11-30 caratteri, tutto attaccato oppure
# a gruppi di 4 separati da uno spazio (solo maiuscole, così le parole che
# seguono non vengono inglobate nel numero).
_IBAN_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z]{2}\d{2}[A-Za-z0-9]{11,30}"
    r"|[A-Z]{2}\d{2}(?: [A-Z0-9]{4}){2,7}(?: [A-Z0-9]{1,4})?)(?![A-Za-z0-9])"
)

# Carta di pagamento: 13-19 cifre, eventualmente separate da spazi o trattini.
_CARD_RE = re.compile(r"(?<![\d.,/])\d(?:[ -]?\d){12,18}(?![\d.,/])")

# Ripetizioni limitate alle lunghezze massime di un indirizzo (64 caratteri prima
# della @, 63 per etichetta del dominio): nessun backtracking costoso su testi
# costruiti apposta (es. lunghe sequenze di "+").
_EMAIL_RE = re.compile(
    r"(?<![\w.+-])[\w.+-]{1,64}@([A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63}){0,8}\.[A-Za-z]{2,24})"
)

# Telefono italiano: prefisso internazionale facoltativo, poi cellulare (3xx) o
# fisso (0x), con spazi/punti/trattini/barre tra le cifre. La lunghezza viene
# controllata dopo (9-11 cifre nazionali), così date come 01/02/2026 (8 cifre)
# e importi non vengono toccati.
_PHONE_RE = re.compile(
    r"(?<![\w.,/+])(?:(?:\+|00)39[ .-]?)?[03]\d(?:[ ./-]?\d){5,10}(?![\w])"
)
_PHONE_PREFIX_RE = re.compile(r"^(?:\+|00)39")


def _iban_ok(candidate: str) -> bool:
    s = re.sub(r"\s", "", candidate).upper()
    if not 15 <= len(s) <= 34:
        return False
    rearranged = s[4:] + s[:4]
    digits = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(digits) % 97 == 1


def _luhn_ok(candidate: str) -> bool:
    digits = [int(d) for d in re.sub(r"\D", "", candidate)]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _phone_ok(candidate: str) -> bool:
    national = _PHONE_PREFIX_RE.sub("", re.sub(r"[ ./-]", "", candidate))
    return 9 <= len(national) <= 11


def _email_kept(domain: str) -> bool:
    domain = domain.lower()
    return any(domain == d or domain.endswith("." + d) for d in PII_KEEP_EMAIL_DOMAINS)


def redact(text: str) -> str:
    """Il testo con i dati personali riconoscibili sostituiti da un segnaposto.
    Con PII_REDACTION disattivo restituisce il testo invariato."""
    if not PII_REDACTION or not text:
        return text
    text = _CF_RE.sub("[codice fiscale]", text)
    text = _IBAN_RE.sub(lambda m: "[IBAN]" if _iban_ok(m.group(0)) else m.group(0), text)
    text = _CARD_RE.sub(lambda m: "[carta di pagamento]" if _luhn_ok(m.group(0)) else m.group(0), text)
    text = _EMAIL_RE.sub(lambda m: m.group(0) if _email_kept(m.group(1)) else "[email]", text)
    text = _PHONE_RE.sub(lambda m: "[telefono]" if _phone_ok(m.group(0)) else m.group(0), text)
    return text


def redact_history(history: list[dict] | None) -> list[dict] | None:
    """Maschera i messaggi dell'utente nella cronologia. Le risposte del
    bot restano intatte: contengono recapiti pubblici degli uffici che servono
    al modello per rispondere alle domande successive."""
    if not history:
        return history
    return [
        {**m, "content": redact(m.get("content", ""))} if m.get("role") == "user" else m
        for m in history
    ]
