import pytest

from api.pii import contains_personal, redact


@pytest.mark.parametrize("text,mask", [
    ("il mio codice RSSMRA80A01H501Z", "[codice fiscale]"),
    ("RSSMRA 80A01 H501Z", "[codice fiscale]"),
    ("IBAN IT60X0542811101000000123456", "[IBAN]"),
    ("IBAN IT60-X054-2811-1010-0000-0123-456", "[IBAN]"),
    ("chiamami al 333 123 4567", "[telefono]"),
    ("tel.3331234567", "[telefono]"),
    ("+44 20 7946 0958", "[telefono]"),
    ("scrivi a mario.rossi@gmail.com", "[email]"),
    ("carta 4111 1111 1111 1111", "[carta di pagamento]"),
])
def test_masked(text, mask):
    assert mask in redact(text)


@pytest.mark.parametrize("text", [
    "scadenza 31/12/2026 importo 1.234,56 euro",
    "legge 2012 n. 190 del 6.11.2012",
    "come pago la TARI?",
])
def test_left_alone(text):
    assert redact(text) == text
    assert not contains_personal(text)


@pytest.mark.parametrize("text", [
    "Mi chiamo Mario Rossi", "mi chiamo mario rossi", "Sono Mario Rossi e abito in via Roma 5",
    "sono nato a Roma", "il mio nome è Mario",
])
def test_blocked(text):
    assert contains_personal(text)


def test_hostile_input_is_fast():
    import time
    start = time.time()
    for t in ["+" * 5000, "00" * 3000, "A1-" * 3000, "ab " * 2000, "9" * 5000]:
        redact(t)
        contains_personal(t)
    assert time.time() - start < 2
