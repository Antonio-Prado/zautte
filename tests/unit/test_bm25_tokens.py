"""BM25 tokens (indexer/vector_store._tokenize): words and numbers of two digits or more, so an act
can be found by its number."""
from indexer.vector_store import _tokenize


def test_words_are_lowercased_and_accents_kept():
    assert _tokenize("Carta d'Identità, perché?") == ["carta", "d", "identità", "perché"]


def test_numbers_of_two_digits_or_more_are_tokens():
    assert _tokenize("determina n. 1200 del 2025") == ["determina", "n", "1200", "del", "2025"]
    assert _tokenize("Numero: 1406/2026; Data: 05/11/2026") == ["numero", "1406", "2026", "data", "05", "11", "2026"]
    assert _tokenize("Regolamento TARI CC 42/2024, art. 5") == ["regolamento", "tari", "cc", "42", "2024", "art"]


def test_single_digits_are_dropped_and_amounts_split():
    assert _tokenize("€ 1.200,00 per 3 anni") == ["200", "00", "per", "anni"]
