from api import rag


def test_synonyms_start_a_word_but_never_match_inside_one(monkeypatch):
    monkeypatch.setattr(rag, "_SYNONYMS", {"cie": ["carta identità"], "rata": ["acconto saldo"],
                                           "nubend": ["matrimonio"]})
    q = "Contributi per anziani non autosufficienti: quando è stata deliberata la misura?"
    assert rag.expand_query(q) == q
    assert rag.expand_query("Rinnovo della CIE").endswith("carta identità")
    assert rag.expand_query("La prima rata scade a giugno?").endswith("acconto saldo")
    assert rag.expand_query("Documenti per i nubendi").endswith("matrimonio")


def test_synonyms_are_not_added_next_to_the_unless_words(monkeypatch):
    monkeypatch.setattr(rag, "_SYNONYMS", {
        "aperto": {"add": ["orari", "apertura", "sportello"], "unless": ["consigli", "sedut"]},
        "orari": ["orario", "apertura"],
        "rotto": {"add": "not a list"},
    })
    q = "Quando è previsto il prossimo consiglio comunale aperto?"
    assert rag.expand_query(q) == q
    assert rag.expand_query("La seduta di lunedì è aperto a tutti?") == "La seduta di lunedì è aperto a tutti?"
    assert rag.expand_query("L'ufficio anagrafe è aperto il sabato?").endswith("orari apertura sportello")
    # the plain list form still works, and a malformed entry is skipped
    assert rag.expand_query("Orari del museo").endswith("orario apertura")
    assert rag.expand_query("Ho rotto il mastello") == "Ho rotto il mastello"
