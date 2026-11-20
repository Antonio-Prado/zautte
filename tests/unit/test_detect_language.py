import pytest

from api.rag import detect_language


@pytest.mark.parametrize("question", [
    "I lost my wallet in the town centre, could someone have handed it in to the Municipality?",
    "My mother is elderly and lives alone, can the Municipality send someone to help her at home?",
    "Can I drive into the old town centre? Do I need a permit?",
    "I have an electric car, can I park for free on the blue lines?",
    "Can I go to the beach with my dog?",
    "Are there beaches equipped for people in wheelchairs?",
    "I am moving abroad for work, do I have to tell the Municipality?",
    "Yes do you speak English?",
    "How much?",
    "Hello",
])
def test_english_questions_without_topic_words(question):
    assert detect_language(question) == "en"


@pytest.mark.parametrize("question", [
    "Ho perso il portafoglio in centro, qualcuno può averlo consegnato al Comune?",
    "per sposarmi invece cosa devo fare?",
    "Vorrei comprare un loculo al cimitero",
    "Come si prende in prestito un libro alla biblioteca?",
    "Dove trovo il wifi gratuito in città?",
    "Non so usare lo SPID, c'è qualcuno che mi aiuta?",
    "quanto costa?",
    "e per un bar invece?",
    "Posso pagare con home banking?",
])
def test_italian_questions(question):
    assert detect_language(question) == "it"


@pytest.mark.parametrize("question", ["", "ok", "TARI?", "SPID", "12345"])
def test_no_evidence_is_italian(question):
    assert detect_language(question) == "it"


def test_no_evidence_takes_the_language_of_the_previous_turns():
    history = [
        {"role": "user", "content": "Where can I park near the seafront?"},
        {"role": "assistant", "content": "Il parcheggio a pagamento è in viale Trieste."},
    ]
    assert detect_language("ok", history) == "en"
    assert detect_language("TARI?", history) == "en"
    # the question itself decides when it has evidence
    assert detect_language("e quanto costa?", history) == "it"
    assert detect_language("ok", [{"role": "assistant", "content": "The office is open."}]) == "it"


@pytest.mark.parametrize("question", [
    "Comment puis-je obtenir un certificat de résidence ?",
    "¿Dónde puedo avisar de una farola que lleva días apagada?",
    "Am pierdut codurile PIN și PUK ale cărții de identitate electronice, ce trebuie să fac?",
])
def test_other_languages_get_the_italian_instructions(question):
    assert detect_language(question) == "it"
