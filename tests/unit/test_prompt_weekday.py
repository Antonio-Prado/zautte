"""The question given to the model carries today's date with the day of the week, so that «questo weekend»
or «domani» need no calendar arithmetic by the model."""
import datetime

from api import rag


def _question(monkeypatch, day, language):
    monkeypatch.setattr(rag, "now_local", lambda: datetime.datetime(*day, 10, 0))
    messages = rag.build_prompt("Cosa c'è questo weekend?", "un brano", language=language)
    return messages[-1]["content"]


def test_italian_date_has_the_weekday(monkeypatch):
    assert "(Data di oggi: venerdì 30/10/2026)\nDOMANDA:" in _question(monkeypatch, (2026, 10, 30), "it")
    assert "(Data di oggi: domenica 01/11/2026)" in _question(monkeypatch, (2026, 11, 1), "it")


def test_english_date_has_the_weekday(monkeypatch):
    assert "(Today's date: Monday 2026-11-02)" in _question(monkeypatch, (2026, 11, 2), "en")
