import json

import pytest
from fastapi import HTTPException

from api import auth, survey

DEF = {
    "id": "pilot", "lang": "it", "title": "T", "exclude_email_domains": ["staff.example.org"],
    "labels": {"submit": "Invia"},
    "questions": [
        {"id": "overall", "type": "scale", "min": 1, "max": 5, "text": "Soddisfatto?"},
        {"id": "correct", "type": "choice", "text": "Corrette?", "options": ["Sì", "No"]},
        {"id": "before", "type": "choice", "multiple": True, "required": False, "text": "Prima?",
         "options": ["Sito", "Telefono", "Sportello"]},
        {"id": "improve", "type": "text", "text": "Migliorare?", "max_length": 50},
        {"id": "broken", "type": "choice", "text": "one option only", "options": ["x"]},
    ],
}
USERS = [{"id": "u1", "email": "anna@example.org"}, {"id": "u2", "email": "bo@example.org"},
         {"id": "s1", "email": "clerk@staff.example.org"}, {"id": "t1", "email": "zz-test@example.org"}]


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg = tmp_path / "survey.json"
    cfg.write_text(json.dumps(DEF), encoding="utf-8")
    monkeypatch.setattr(survey, "CONFIG_FILE", cfg)
    monkeypatch.setattr(survey, "ANSWERS_FILE", tmp_path / "survey.jsonl")
    monkeypatch.setattr(survey, "RESPONDENTS_FILE", tmp_path / "survey_respondents.json")
    monkeypatch.setattr(survey, "TEST_ACCOUNTS", frozenset({"zz-test@example.org"}))
    monkeypatch.setattr(survey, "get_user_by_id", lambda uid: next((u for u in USERS if u["id"] == uid), None))
    monkeypatch.setattr(auth, "AUTH_SECRET", "s" * 32)
    return tmp_path


GOOD = {"overall": 4, "correct": "Sì", "before": ["Telefono", "Sito"], "improve": "  più   orari  "}


def test_definition_drops_broken_questions_and_merges_labels(env):
    d = survey.load_definition()
    assert [q["id"] for q in d["questions"]] == ["overall", "correct", "before", "improve"]
    assert d["questions"][3]["required"] is False          # text questions are optional by default
    assert d["labels"]["submit"] == "Invia" and d["labels"]["sending"] == survey.DEFAULT_LABELS["sending"]
    assert "exclude_email_domains" not in survey.public_definition(d)


def test_validation(env):
    d = survey.load_definition()
    out = survey.validate_answers(d, GOOD)
    assert out == {"overall": 4, "correct": "Sì", "before": ["Sito", "Telefono"], "improve": "più orari"}
    for bad in ({**GOOD, "overall": 6}, {**GOOD, "overall": True}, {**GOOD, "correct": "Forse"},
                {**GOOD, "correct": ["Sì", "No"]}, {**GOOD, "before": ["Sito", "Sito"]},
                {**GOOD, "nope": 1}, {k: v for k, v in GOOD.items() if k != "overall"}):
        with pytest.raises(ValueError):
            survey.validate_answers(d, bad)
    assert survey.validate_answers(d, {**GOOD, "improve": "x" * 80})["improve"] == "x" * 50


def test_answers_are_anonymous_and_once_per_account(env):
    d = survey.load_definition()
    survey.store(d, "u1", survey.validate_answers(d, GOOD))
    with pytest.raises(FileExistsError):
        survey.store(d, "u1", survey.validate_answers(d, GOOD))
    survey.store(d, "u2", survey.validate_answers(d, {**GOOD, "overall": 2, "before": []}))
    raw = (env / "survey.jsonl").read_text()
    assert "u1" not in raw and "anna" not in raw and '"ts"' not in raw
    keys = json.loads((env / "survey_respondents.json").read_text())["pilot"]
    assert keys == sorted(keys) and len(keys) == 2 and not any("u1" in k for k in keys)
    assert survey.has_answered("pilot", "u1") and not survey.has_answered("pilot", "s1")

    r = survey.results(d)
    assert r["responses"] == 2
    q = {x["id"]: x for x in r["questions"]}
    assert q["overall"]["average"] == 3.0 and q["overall"]["counts"]["4"] == 1
    assert q["before"]["answered"] == 1 and q["before"]["counts"] == {"Sito": 1, "Telefono": 1, "Sportello": 0}
    assert q["improve"]["texts"] == ["più orari", "più orari"]


def test_test_accounts_repeat_and_are_not_counted(env):
    d = survey.load_definition()
    for _ in range(2):
        survey.store(d, "t1", survey.validate_answers(d, GOOD))
    r = survey.results(d)
    assert r["responses"] == 0 and r["test_responses"] == 2
    assert not (env / "survey_respondents.json").exists()


def test_eligibility_excludes_domains_but_not_tests(env, monkeypatch):
    d = survey.load_definition()
    assert survey.eligible(d, "u1") and not survey.eligible(d, "s1")
    USERS.append({"id": "t2", "email": "zz-test@staff.example.org"})
    monkeypatch.setattr(survey, "TEST_ACCOUNTS", frozenset({"zz-test@staff.example.org"}))
    try:
        assert survey.eligible(d, "t2")
    finally:
        USERS.pop()


def test_personal_data_in_free_text_is_dropped(env):
    d = survey.load_definition()
    out = survey.validate_answers(d, {**GOOD, "improve": "scrivetemi a mario@example.org"})
    assert "mario" not in out["improve"]


def test_missing_config_is_404(env, monkeypatch):
    monkeypatch.setattr(survey, "CONFIG_FILE", env / "absent.json")
    with pytest.raises(HTTPException) as e:
        survey._definition_or_404()
    assert e.value.status_code == 404


def test_closing_day_is_included_then_closed(env, monkeypatch):
    import datetime as dt
    cfg = env / "survey.json"
    cfg.write_text(json.dumps({**DEF, "closes": "2026-11-25"}), encoding="utf-8")
    tz = dt.timezone(dt.timedelta(hours=1))
    monkeypatch.setattr(survey, "now_local", lambda: dt.datetime(2026, 11, 25, 23, 59, tzinfo=tz))
    assert survey.load_definition()["open"] is True
    monkeypatch.setattr(survey, "now_local", lambda: dt.datetime(2026, 11, 26, 0, 1, tzinfo=tz))
    assert survey.load_definition()["open"] is False
    cfg.write_text(json.dumps({**DEF, "closes": "25/11/2026"}), encoding="utf-8")
    assert survey.load_definition()["open"] is False   # a malformed date closes rather than leaving it open


def test_empty_respondents_file_created_at_startup(env):
    # ensure_private creates the data files empty when the API starts
    (env / "survey_respondents.json").write_text("")
    (env / "survey.jsonl").write_text("")
    d = survey.load_definition()
    assert not survey.has_answered("pilot", "u1")
    survey.store(d, "u1", survey.validate_answers(d, GOOD))
    assert survey.has_answered("pilot", "u1") and survey.results(d)["responses"] == 1
