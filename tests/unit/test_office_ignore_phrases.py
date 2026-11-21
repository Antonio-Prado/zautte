"""An office is not suggested for a keyword that is part of a name (offices.json, ignore_phrases)."""
import json

from api import rag


def _offices(tmp_path, monkeypatch, entries):
    config = tmp_path / "config"
    config.mkdir()
    (config / "offices.json").write_text(json.dumps(entries), encoding="utf-8")
    (tmp_path / "api").mkdir()
    monkeypatch.setattr(rag, "__file__", str(tmp_path / "api" / "rag.py"))
    monkeypatch.setattr(rag, "_OFFICE_MAP", rag._load_office_map())


PARKS = {"keywords": ["palme", "alberi"], "ignore_phrases": ["cinema delle palme", "riviera  delle palme"],
         "name": "Aree verdi", "url": "https://example.org/verde"}


def test_a_keyword_inside_a_name_does_not_call_the_office(tmp_path, monkeypatch):
    _offices(tmp_path, monkeypatch, [PARKS])
    assert rag.suggest_office("Quando è stato chiuso il Cinema delle Palme?") is None
    assert rag.suggest_office("Dove si trova lo stadio Riviera delle Palme?") is None
    assert "Aree verdi" in rag.suggest_office("Le palme del lungomare sono malate")
    # the rest of the question still counts
    assert "Aree verdi" in rag.suggest_office("Davanti al cinema delle palme ci sono alberi da potare")


def test_offices_without_ignore_phrases_match_as_before(tmp_path, monkeypatch):
    _offices(tmp_path, monkeypatch, [{"keywords": ["palme"], "name": "Aree verdi", "url": "https://example.org/verde"}])
    assert "Aree verdi" in rag.suggest_office("Cinema delle Palme")
