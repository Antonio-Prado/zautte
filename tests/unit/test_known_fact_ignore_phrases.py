from api import rag

CERTIFICATES = {
    "keywords": ["certificato", "stato", "famiglia", "storico", "libero"],
    "require_any": ["certificato", "storico", "libero"],
    "ignore_phrases": ["centro storico", "libero  professionista"],
    "title": "Certificates", "source": "https://example.org/it/services/certificates", "text": "...",
}
PLAIN = {"keywords": ["storico"], "require_any": ["storico"], "title": "Plain", "source": "", "text": "..."}


def _titles(query, entries):
    old = rag._KNOWN_FACTS
    rag._KNOWN_FACTS = rag._parse_known_facts(entries)
    try:
        return [c["title"] for c in rag._inject_known_facts(query)]
    finally:
        rag._KNOWN_FACTS = old


def test_an_ignored_phrase_does_not_trigger_the_fact():
    q = "Canna fumaria esterna per un'attività commerciale in Centro  Storico"
    assert _titles(q, [CERTIFICATES]) == []
    assert _titles("Posso occupare uno spazio pubblico da libero professionista?", [CERTIFICATES]) == []


def test_the_same_word_outside_the_phrase_still_triggers_it():
    assert _titles("Come ottengo uno stato di famiglia storico?", [CERTIFICATES]) == ["Certificates"]
    assert _titles("Certificato di residenza per un negozio in centro storico", [CERTIFICATES]) == ["Certificates"]


def test_a_phrase_is_matched_as_whole_words_only():
    assert _titles("Stato di famiglia storico del centro storicoX", [CERTIFICATES]) == ["Certificates"]


def test_facts_without_ignore_phrases_match_as_before():
    assert _titles("Il centro storico", [PLAIN]) == ["Plain"]
