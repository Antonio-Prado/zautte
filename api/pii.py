"""
Masking of recognisable personal data in question text.

The visitor's question reaches the language model (external provider),
the local logs (usage.jsonl, gaps.jsonl, feedback.jsonl, stats.json) and the
application log. The widget asks users not to enter personal data but cannot
prevent it: here data in a recognisable format is replaced by a
placeholder BEFORE any use.

Masked: tax code (codice fiscale), IBAN (with check-digit verification), payment
card numbers (with Luhn verification), email addresses (except those of the
institutional domains in PII_KEEP_EMAIL_DOMAINS) and Italian phone numbers.
Names, home addresses and other free-form data cannot be recognised
reliably and remain: the notice in the widget is still necessary.

With PII_BLOCK a question containing personal data (the kinds recognised above,
or a first-person introduction: name, home address, date
and place of birth) is not processed at all: contains_personal()
intercepts it and the API answers with BLOCK_MESSAGE, without sending it to the
model provider and without recording the text (DPO opinion of 19/10/2026).
"""

import re

from config.settings import PII_BLOCK, PII_KEEP_EMAIL_DOMAINS, PII_REDACTION

# Tax code (codice fiscale) of natural persons, including omocodia (digits replaced
# by the letters LMNPQRSTUV).
_CF_RE = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z]{6}[0-9LMNPQRSTUVlmnpqrstuv]{2}[ABCDEHLMPRSTabcdehlmprst]"
    r"[0-9LMNPQRSTUVlmnpqrstuv]{2}[A-Za-z][0-9LMNPQRSTUVlmnpqrstuv]{3}[A-Za-z](?![A-Za-z0-9])"
)

# IBAN: country + 2 check digits + 11-30 characters, either contiguous or
# in groups of 4 separated by a space (uppercase only, so that the words that
# follow are not swallowed into the number).
_IBAN_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z]{2}\d{2}[A-Za-z0-9]{11,30}"
    r"|[A-Z]{2}\d{2}(?: [A-Z0-9]{4}){2,7}(?: [A-Z0-9]{1,4})?)(?![A-Za-z0-9])"
)

# Payment card: 13-19 digits, optionally separated by spaces or hyphens.
_CARD_RE = re.compile(r"(?<![\d.,/])\d(?:[ -]?\d){12,18}(?![\d.,/])")

# Repetitions bounded by the maximum lengths of an address (64 characters before
# the @, 63 per domain label): no costly backtracking on deliberately
# crafted text (e.g. long runs of "+").
_EMAIL_RE = re.compile(
    r"(?<![\w.+-])([\w.+-]{1,64})@([A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63}){0,8}\.[A-Za-z]{2,24})"
)

# Italian phone: optional international prefix, then mobile (3xx) or
# landline (0x), with spaces/dots/hyphens/slashes between digits. The length is
# checked afterwards (9-11 national digits), so dates like 01/02/2026 (8 digits)
# and amounts are left untouched.
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


_ROLE_WORDS = {
    "ufficio", "uff", "urp", "protocollo", "pec", "info", "segreteria", "servizio", "servizi", "settore",
    "tributi", "anagrafe", "stato", "civile", "polizia", "municipale", "sindaco", "giunta", "comune",
    "ragioneria", "personale", "cultura", "scuola", "sociale", "turismo", "suap", "urbanistica", "lavori",
    "pubblici", "ambiente", "sport", "biblioteca", "elettorale", "contratti", "economato", "avvocatura",
    "segretario", "generale", "dirigente", "area", "direzione", "staff", "gabinetto", "stampa", "cimitero",
}


def _email_kept(domain: str) -> bool:
    domain = domain.lower()
    return any(domain == d or domain.endswith("." + d) for d in PII_KEEP_EMAIL_DOMAINS)


def _personal_mailbox(local: str) -> bool:
    """name.surname@ style: the address of a person, not of an office."""
    parts = [p for p in re.split(r"[._-]", local.lower()) if p]
    return len(parts) >= 2 and all(p.isalpha() for p in parts) and not (set(parts) & _ROLE_WORDS)


def _mask(text: str, keep_institutional: bool) -> str:
    text = _CF_RE.sub("[codice fiscale]", text)
    text = _IBAN_RE.sub(lambda m: "[IBAN]" if _iban_ok(m.group(0)) else m.group(0), text)
    text = _CARD_RE.sub(lambda m: "[carta di pagamento]" if _luhn_ok(m.group(0)) else m.group(0), text)
    text = _EMAIL_RE.sub(
        lambda m: m.group(0) if keep_institutional and _email_kept(m.group(2)) else "[email]", text)
    return _PHONE_RE.sub(lambda m: "[telefono]" if _phone_ok(m.group(0)) else m.group(0), text)


def redact(text: str) -> str:
    """The text with recognisable personal data replaced by a placeholder.
    With PII_REDACTION off it returns the text unchanged."""
    if not PII_REDACTION or not text:
        return text
    return _mask(text, keep_institutional=True)


# First-person introductions. Proper names require an initial capital
# (the words following «sono» are often common words: «sono residente», «sono
# anziano»), hence the patterns are case-sensitive.
_NAME = r"[A-ZÀ-Ý][a-zà-ÿ']{1,}"
_SELF_RE = re.compile(
    r"(?i:\bmi chiamo|\bil mio nome è|\bil mio cognome è|\bme chiamo|\bmy name is|\bi am called)\s+" + _NAME
    + r"|(?i:\bsono (?:il |la |lo )?(?:signor|signora|sig\.|sig\.ra|dott\.|dottor|dottoressa|ing\.)?)\s*" + _NAME + r"\s+" + _NAME
    + r"|(?i:\b(?:abito|vivo|risiedo|sono residente|resido|domiciliat[oa]) (?:in|a) (?:via|viale|corso|piazza|largo|vicolo|contrada|strada)\b)[^.?!]{0,40}\d"
    + r"|(?i:\bsono nat[oa] (?:a|il)\b)"
)

BLOCK_MESSAGE = (
    "Per proteggere la tua privacy, in questa fase di sperimentazione Zautte non può "
    "trattare dati personali (nome e cognome, codice fiscale, indirizzo di residenza, "
    "email, telefono, IBAN, data e luogo di nascita). La tua domanda non è stata "
    "elaborata né conservata.\n\n"
    "Riformula la domanda senza questi dati: per esempio, invece di «Sono Mario Rossi e "
    "abito in via Roma 5, come pago la TARI?» scrivi «Come pago la TARI?». "
    "Per pratiche che riguardano la tua persona rivolgiti direttamente agli uffici del Comune."
)


def contains_personal(text: str) -> bool:
    """True if the text contains recognisable personal data or a
    first-person introduction (see PII_BLOCK). Always False with blocking
    off."""
    if not PII_BLOCK or not text:
        return False
    # Detection does not depend on PII_REDACTION, and an institutional address counts when it is a
    # person's (name.surname@), not an office's
    if _mask(text, keep_institutional=False) != text and (
            _mask(text, keep_institutional=True) != text
            or any(_personal_mailbox(m.group(1)) and _email_kept(m.group(2)) for m in _EMAIL_RE.finditer(text))):
        return True
    return bool(_SELF_RE.search(text))
