"""Idle limit of a session (AUTH_IDLE_MINUTES): a token lasts that long, a request renews it,
the login bounds the whole session and a logout revokes every token renewed from it."""
from types import SimpleNamespace

import pytest

from api import auth


@pytest.fixture
def session(monkeypatch, tmp_path):
    clock = SimpleNamespace(now=1_800_000_000)
    monkeypatch.setattr(auth.time, "time", lambda: clock.now)
    monkeypatch.setattr(auth, "AUTH_SECRET", "s" * 32)
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "AUTH_IDLE_MINUTES", 30)
    monkeypatch.setattr(auth, "AUTH_TOKEN_TTL_DAYS", 7)
    monkeypatch.setattr(auth, "_users_cached", lambda: [{"id": "u1", "name": "Anna"}])
    monkeypatch.setattr(auth, "_REVOKED_FILE", tmp_path / "revoked_tokens.json")
    monkeypatch.setattr(auth, "_revoked_state", {"mtime": None, "keys": {}})
    return clock


def _request(path="/chat"):
    return SimpleNamespace(state=SimpleNamespace(), url=SimpleNamespace(path=path))


def test_token_ends_after_the_idle_limit(session):
    token = auth.create_token("u1", "Anna")
    session.now += 29 * 60
    assert auth.verify_token(token)["uid"] == "u1"
    session.now += 2 * 60
    assert auth.verify_token(token) is None


def test_a_request_renews_the_session(session):
    token = auth.create_token("u1", "Anna")
    session.now += 20 * 60
    request = _request()
    first = auth.verify_token(token)
    auth.note_activity(request, first)
    renewed = request.state.auth_renewed
    session.now += 20 * 60                      # 40 minutes after the login
    assert auth.verify_token(token) is None
    claims = auth.verify_token(renewed)
    assert claims["sid"] == first["sid"] and claims["jti"] != first["jti"]
    assert claims["start"] == first["start"] == 1_800_000_000


def test_a_token_just_issued_is_not_renewed(session):
    token = auth.create_token("u1", "Anna")
    session.now += 30
    request = _request()
    auth.note_activity(request, auth.verify_token(token))
    assert not hasattr(request.state, "auth_renewed")


def test_the_login_bounds_the_session_however_often_it_is_renewed(session):
    token = auth.create_token("u1", "Anna")
    end = session.now + 7 * 86400
    while session.now + 20 * 60 < end:
        session.now += 20 * 60
        token = auth.renewed_token(auth.verify_token(token))
    assert auth.verify_token(token)["exp"] == end
    session.now = end + 1
    assert auth.verify_token(token) is None


def test_logout_is_not_use_and_revokes_the_renewed_tokens_too(session):
    token = auth.create_token("u1", "Anna")
    session.now += 10 * 60
    renewed = auth.renewed_token(auth.verify_token(token))
    request = _request("/auth/logout")
    auth.note_activity(request, auth.verify_token(token))
    assert not hasattr(request.state, "auth_renewed")
    auth.revoke_token(auth.verify_token(token))
    assert auth.verify_token(token) is None and auth.verify_token(renewed) is None


def test_a_long_token_issued_before_the_limit_ends_with_it(session, monkeypatch):
    monkeypatch.setattr(auth, "AUTH_IDLE_MINUTES", 0)
    token = auth.create_token("u1", "Anna")       # seven days, as before
    session.now += 3600
    assert auth.verify_token(token) is not None
    assert auth.renewed_token(auth.verify_token(token)) is None   # no idle limit: nothing to renew
    monkeypatch.setattr(auth, "AUTH_IDLE_MINUTES", 30)
    assert auth.verify_token(token) is None


def test_header_reaches_the_client_only_on_success(session, monkeypatch):
    from fastapi.testclient import TestClient

    from api import main
    client = TestClient(main.app)
    token = auth.create_token("u1", "Anna")
    session.now += 5 * 60
    ok = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 200
    renewed = ok.headers[auth.RENEWED_TOKEN_HEADER]
    assert auth.verify_token(renewed)["uid"] == "u1"
    assert auth.RENEWED_TOKEN_HEADER not in client.get("/auth/me").headers
    out = client.post("/auth/logout", headers={"Authorization": f"Bearer {renewed}"})
    assert out.status_code == 200 and auth.RENEWED_TOKEN_HEADER not in out.headers
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
