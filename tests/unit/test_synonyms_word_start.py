from api import rag


def test_synonyms_start_a_word_but_never_match_inside_one(monkeypatch):
    monkeypatch.setattr(rag, "_SYNONYMS", {"cie": ["carta identità"], "rata": ["acconto saldo"],
                                           "nubend": ["matrimonio"]})
    q = "Contributi per anziani non autosufficienti: quando è stata deliberata la misura?"
    assert rag.expand_query(q) == q
    assert rag.expand_query("Rinnovo della CIE").endswith("carta identità")
    assert rag.expand_query("La prima rata scade a giugno?").endswith("acconto saldo")
    assert rag.expand_query("Documenti per i nubendi").endswith("matrimonio")
