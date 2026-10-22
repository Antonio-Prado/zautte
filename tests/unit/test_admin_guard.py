from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api import admin_guard as ag


def _req(host="203.0.113.9"):
    return SimpleNamespace(client=SimpleNamespace(host=host), headers={})


@pytest.fixture(autouse=True)
def _strong_key(monkeypatch):
    monkeypatch.setattr(ag, "ADMIN_API_KEY", "k" * 32)
    monkeypatch.setattr(ag, "ADMIN_AUTH_DISABLED", False)
    ag._failures.clear()
    ag._all_failures.clear()


def test_wrong_keys_lock_the_address_but_not_requests_without_a_key():
    for _ in range(ag.ADMIN_MAX_ATTEMPTS):
        assert ag.check_admin_key(_req(), "wrong") is False
    with pytest.raises(HTTPException) as e:
        ag.check_admin_key(_req(), "k" * 32)
    assert e.value.status_code == 429
    assert ag.check_admin_key(_req(), None) is False   # the public /health answer, not 429


def test_global_block_spares_requests_without_a_key(monkeypatch):
    monkeypatch.setattr(ag, "ADMIN_GLOBAL_MAX_FAILURES", 3)
    for i in range(3):
        ag.check_admin_key(_req(f"203.0.113.{i}"), "wrong")
    with pytest.raises(HTTPException):
        ag.check_admin_key(_req("203.0.113.99"), "k" * 32)
    assert ag.check_admin_key(_req("203.0.113.99"), None) is False
