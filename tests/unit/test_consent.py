import asyncio

import pytest
from fastapi import HTTPException

from api import auth


@pytest.fixture
def users(monkeypatch):
    rows = [{"id": "a", "email": "a@x", "consent": {"version": "v2", "adult": True, "notice_read": True}},
            {"id": "b", "email": "b@x", "consent": {"version": "v1", "adult": True, "notice_read": True}},
            {"id": "c", "email": "c@x"}]
    monkeypatch.setattr(auth, "_users_cached", lambda: rows)
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "CONSENT_REQUIRED", True)
    monkeypatch.setattr(auth, "CONSENT_VERSION", "v2")
    return rows


def test_only_the_current_version_counts(users):
    assert auth.consent_ok("a")
    assert not auth.consent_ok("b")   # an older notice
    assert not auth.consent_ok("c")   # never declared
    assert not auth.consent_ok("zz")


def test_nothing_is_asked_when_the_installation_does_not_require_it(users, monkeypatch):
    monkeypatch.setattr(auth, "CONSENT_REQUIRED", False)
    assert auth.consent_ok("c")


def test_the_chat_dependency_refuses_with_a_named_error(users):
    with pytest.raises(HTTPException) as e:
        asyncio.run(auth.require_participant({"uid": "c"}))
    assert e.value.status_code == 403 and e.value.detail == {"error": "consent_required"}
    assert asyncio.run(auth.require_participant({"uid": "a"})) == {"uid": "a"}
