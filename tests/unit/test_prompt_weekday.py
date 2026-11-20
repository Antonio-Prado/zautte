"""The question given to the model carries today's date with the day of the week, so that «questo weekend»
or «domani» need no calendar arithmetic by the model."""
import datetime

from api import rag


def _question(monkeypatch, day, language):
    monkeypatch.setattr(rag, "now_local", lambda: datetime.datetime(*day, 10, 0, tzinfo=datetime.UTC))
    messages = rag.build_prompt("Cosa c'è questo weekend?", "un brano", language=language)
    return messages[-1]["content"]


def test_italian_date_has_the_weekday(monkeypatch):
    assert "(Data di oggi: venerdì 30/10/2026)\nDOMANDA:" in _question(monkeypatch, (2026, 10, 30), "it")
    assert "(Data di oggi: domenica 01/11/2026)" in _question(monkeypatch, (2026, 11, 1), "it")


def test_the_language_of_the_answer_is_said_next_to_the_question(monkeypatch):
    it = _question(monkeypatch, (2026, 10, 30), "it")
    assert "(Scrivi la risposta in italiano, in qualunque lingua sia la domanda.)\n(Data di oggi:" in it
    en = _question(monkeypatch, (2026, 11, 2), "en")
    assert "(Write the answer in English, whatever the language of the question.)\n(Today's date:" in en
    assert "in italiano" in rag.SYSTEM_PROMPT_IT.split("Regole FONDAMENTALI:")[1].splitlines()[1]
    assert "in English" in rag.SYSTEM_PROMPT_EN.split("Rules:")[1].splitlines()[1]


def test_english_date_has_the_weekday(monkeypatch):
    assert "(Today's date: Monday 2026-11-02)" in _question(monkeypatch, (2026, 11, 2), "en")
