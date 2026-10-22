import json

import pytest

from api import applications as apps
from api import auth, datalog


@pytest.fixture
def files(tmp_path, monkeypatch):
    a = tmp_path / "applications.jsonl"
    u = tmp_path / "users.json"
    monkeypatch.setattr(apps, "APPLICATIONS_FILE", a)
    monkeypatch.setattr(datalog, "APPLICATIONS_FILE", a)
    monkeypatch.setattr(apps, "USERS_FILE", u)
    monkeypatch.setattr(auth, "USERS_FILE", u)
    monkeypatch.setattr(auth, "AUTH_SECRET", "s" * 32)
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "PUBLIC_BASE_URL", "https://bot.example.org")
    u.write_text("[]", encoding="utf-8")
    return a, u


def _req(**over):
    base = {"name": "Mario Rossi", "email": "Mario.Rossi@example.org", "age": 40, "district": "Centro",
            "usage": "sportello", "adult": True, "notice_read": True, "website": ""}
    return apps.ApplyRequest(**{**base, **over})


def test_validation_refuses_robots_and_half_declarations():
    assert apps.validate(_req()) is None
    assert apps.validate(_req(website="http://spam")) == "spam"
    assert apps.validate(_req(notice_read=False))
    assert apps.validate(_req(email="not-an-email"))


def test_store_and_accept_create_the_account_once(files):
    a, u = files
    entry = apps.store(_req())
    assert entry["email"] == "mario.rossi@example.org" and entry["status"] == "new"
    assert [json.loads(ln) for ln in a.read_text().split("\n") if ln.strip()] == [entry]

    user, link = apps.accept(entry["id"])
    assert user["email"] == "mario.rossi@example.org" and link.startswith("https://bot.example.org")
    assert json.loads(u.read_text())[0]["email"] == "mario.rossi@example.org"
    apps.mark(entry["id"], "accepted")
    assert apps.list_all()[0]["status"] == "accepted"
    with pytest.raises(ValueError):
        apps.accept(entry["id"])   # already accepted
    second = apps.store(_req())
    with pytest.raises(ValueError):
        apps.accept(second["id"])  # the email already has an account


def test_age_below_18_is_refused_by_the_model():
    with pytest.raises(ValueError):
        _req(age=17)
