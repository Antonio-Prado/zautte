"""Accounts listed in DAILY_LIMIT_EXEMPT are not subject to the daily limit: by id or e-mail,
never by name (the name is typed by the applicant in the public application form)."""
from api import quota


def test_exempt_by_id_or_email_case_insensitive(monkeypatch):
    monkeypatch.setattr(quota, "DAILY_LIMIT_EXEMPT", frozenset({"zz-test", "tester@example.org"}))
    monkeypatch.setattr(quota, "DAILY_QUESTION_LIMIT", 10)
    monkeypatch.setattr(quota, "_email_of", lambda uid: "")
    assert quota.exempt({"uid": "zz-test", "name": "Prova Zautte (zz-test)"})   # by id, as in the token
    assert quota.exempt({"uid": "2", "name": "Mario", "email": "Tester@Example.org"})
    assert not quota.exempt({"uid": "3", "name": "Mario Rossi", "email": "m@example.org"})
    assert quota.limit_for({"id": "zz-test"}) == 0
    assert quota.limit_for({"id": "4", "name": "Mario Rossi"}) == 10


def test_a_chosen_name_never_exempts(monkeypatch):
    monkeypatch.setattr(quota, "DAILY_LIMIT_EXEMPT", frozenset({"zz-test", "test"}))
    monkeypatch.setattr(quota, "_email_of", lambda uid: "")
    assert not quota.exempt({"uid": "a1b2c3", "name": "ZZ-Test"})
    assert not quota.exempt({"id": "a1b2c3", "name": "Test", "email": "x@example.org"})


def test_token_exempt_by_the_e_mail_of_its_account(monkeypatch):
    """The session token carries the id, not the e-mail: the e-mail is looked up by id."""
    monkeypatch.setattr(quota, "DAILY_LIMIT_EXEMPT", frozenset({"tester@example.org"}))
    monkeypatch.setattr(quota, "_email_of", lambda uid: "tester@example.org" if uid == "u9" else "")
    assert quota.exempt({"uid": "u9", "name": "Chiunque"})
    assert not quota.exempt({"uid": "u8", "name": "tester@example.org"})


def test_no_list_no_exemption(monkeypatch):
    monkeypatch.setattr(quota, "DAILY_LIMIT_EXEMPT", frozenset())
    assert not quota.exempt({"uid": "zz-test"})
