"""Accounts listed in DAILY_LIMIT_EXEMPT are not subject to the daily limit."""
from api import quota


def test_exempt_by_name_or_email_case_insensitive(monkeypatch):
    monkeypatch.setattr(quota, "DAILY_LIMIT_EXEMPT", frozenset({"zz-test", "tester@example.org"}))
    monkeypatch.setattr(quota, "DAILY_QUESTION_LIMIT", 10)
    assert quota.exempt({"uid": "1", "name": "ZZ-Test"})
    assert quota.exempt({"uid": "zz-test", "name": "Prova Zautte (zz-test)"})   # by id, as in the token
    assert quota.exempt({"uid": "2", "name": "Mario", "email": "Tester@Example.org"})
    assert not quota.exempt({"uid": "3", "name": "Mario Rossi", "email": "m@example.org"})
    assert quota.limit_for({"name": "zz-test"}) == 0
    assert quota.limit_for({"name": "Mario Rossi"}) == 10


def test_no_list_no_exemption(monkeypatch):
    monkeypatch.setattr(quota, "DAILY_LIMIT_EXEMPT", frozenset())
    assert not quota.exempt({"name": "zz-test"})
