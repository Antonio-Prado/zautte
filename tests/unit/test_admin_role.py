"""Administrator role: an account with "admin": true opens the restricted area with its own login."""
from types import SimpleNamespace

import pytest
from fastapi.security import HTTPAuthorizationCredentials

from api import admin_guard as ag
from api import auth


@pytest.fixture
def users(monkeypatch):
    accounts = [
        {"id": "a1", "name": "Admin One", "email": "a@example.org", "admin": True},
        {"id": "u1", "name": "User One", "email": "u@example.org"},
    ]
    monkeypatch.setattr(auth, "AUTH_SECRET", "s" * 32)
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "_users_cached", lambda: accounts)
    monkeypatch.setattr(auth, "_revoked_keys", dict)
    monkeypatch.setattr(ag, "ADMIN_API_KEY", "k" * 32)
    monkeypatch.setattr(ag, "ADMIN_AUTH_DISABLED", False)
    ag._failures.clear()
    ag._all_failures.clear()
    ag._blocked_until.clear()
    return accounts


def _req():
    return SimpleNamespace(client=SimpleNamespace(host="203.0.113.9"), headers={})


def _bearer(token):
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def test_only_an_account_with_the_role_gets_admin_claims(users):
    assert auth.admin_claims(auth.create_token("a1", "Admin One", 0))["uid"] == "a1"
    assert auth.admin_claims(auth.create_token("u1", "User One", 0)) is None
    assert auth.admin_claims("garbage") is None


def test_taking_the_role_away_closes_the_area_at_once(users):
    token = auth.create_token("a1", "Admin One", 0)
    users[0].pop("admin")
    assert auth.admin_claims(token) is None
    users[0]["admin"] = "yes"   # only the boolean true counts
    assert auth.admin_claims(token) is None


def test_no_admin_session_while_logins_are_off(users, monkeypatch):
    token = auth.create_token("a1", "Admin One", 0)
    monkeypatch.setattr(auth, "AUTH_ENABLED", False)
    assert auth.admin_claims(token) is None


def test_admin_identity_session_or_key(users):
    from api.main import admin_identity
    who = admin_identity(_req(), None, _bearer(auth.create_token("a1", "Admin One", 0)))
    assert who == "Admin One (a1)"
    # a session without the role does not open it, the key still does
    assert admin_identity(_req(), None, _bearer(auth.create_token("u1", "User One", 0))) is None
    assert admin_identity(_req(), "k" * 32, _bearer(auth.create_token("u1", "User One", 0))) == "admin key"
    assert admin_identity(_req(), None, None) is None
