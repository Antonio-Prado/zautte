import asyncio

from api.rag import guard_phones, guard_phones_stream, phone_digits_given

FACT = ("Centralino 0735 7941. SUE: sue-informazioni@comunesbt.it, tel. 0735 794451. "
        "Mobilità 0735 794234-235. Centro Giovani tel. 349 2745670. Servizi Sociali: Marina Pignotti 794557.")
GIVEN = phone_digits_given([{"role": "user", "content": "CONTESTO: " + FACT}])
NOTE = "(numero non presente nelle fonti)"


def test_invented_number_is_replaced():
    out = guard_phones("- **Tel.** 0735 794111 (centralino)", GIVEN)
    assert "794111" not in out and NOTE in out


def test_numbers_given_to_the_model_stay():
    for text in ("Chiama lo 0735 7941.", "tel. 0735 794451, oppure", "349 2745670",
                 "0735 794235", "+39 0735 794451", "0735 794557", "0735-794451"):
        assert guard_phones(text, GIVEN) == text, text


def test_invented_number_before_a_comma_is_caught():
    assert NOTE in guard_phones("tel. 0735 794999, e-mail", GIVEN)


def test_dates_times_amounts_and_short_numbers_are_left_alone():
    for text in ("entro il 03.11.2026", "dalle 08.30-13.00", "un contributo di 800.000 euro",
                 "chiama il 112", "DGC n. 133 del 23/09/2026", "0,50 euro", "CAP 63074",
                 "https://x.example.org/s3/6115/044066p357ds1090.pdf"):
        assert guard_phones(text, GIVEN) == text, text


def test_english_note():
    assert "(number not in the sources)" in guard_phones("call 0735 794111", GIVEN, "en")


def _stream(tokens):
    async def gen():
        for t in tokens:
            yield t

    async def collect():
        return "".join([t async for t in guard_phones_stream(gen(), GIVEN)])
    return asyncio.run(collect())


def test_number_split_across_tokens_is_checked_whole():
    out = _stream(["Tel. 07", "35 79", "41", "11 (centr", "alino) e 0735 79", "4451."])
    assert out == f"Tel. {NOTE} (centralino) e 0735 794451."


def test_stream_ending_with_a_number():
    assert _stream(["Chiama lo 0735 ", "7941"]) == "Chiama lo 0735 7941"
    assert _stream(["Chiama lo 0735 ", "794000"]) == f"Chiama lo {NOTE}"
