"""Maintenance mode: entered on provider outages only, neutral towards visitors, alerts
the administrator at most once an hour, closes itself after a successful call."""
import anthropic
import pytest

try:
    import httpx2 as httpx
except ImportError:  # anthropic < 2 (httpx)
    import httpx

from api import maintenance


def _err(cls, status, message):
    resp = httpx.Response(status, request=httpx.Request("POST", "https://api.example"))
    return cls(message, response=resp, body={"error": {"message": message}})


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(maintenance, "_state", {"until": 0.0, "since": None, "reason": "", "last_alert": 0.0})
    sent = []
    monkeypatch.setattr(maintenance, "_notify", lambda subject, body: sent.append((subject, body)))
    monkeypatch.setattr(maintenance, "MAINTENANCE_HOLD_SECONDS", 600)
    yield sent


def test_classify_knows_the_outages():
    reason = maintenance.classify(
        _err(anthropic.BadRequestError, 400, "Your credit balance is too low to access the Anthropic API."))
    assert reason == "API credit exhausted"      # fixed text: the provider's message never leaves the log
    assert "not valid" in maintenance.classify(_err(anthropic.AuthenticationError, 401, "invalid x-api-key"))
    assert "permission" in maintenance.classify(_err(anthropic.PermissionDeniedError, 403, "no access"))
    assert "402" in maintenance.classify(_err(anthropic.APIStatusError, 402, "payment required"))


def test_classify_ignores_ordinary_errors():
    assert maintenance.classify(_err(anthropic.BadRequestError, 400, "max_tokens too large")) is None
    assert maintenance.classify(_err(anthropic.RateLimitError, 429, "rate limit")) is None
    assert maintenance.classify(_err(anthropic.InternalServerError, 500, "overloaded")) is None
    assert maintenance.classify(ValueError("x")) is None


def test_activate_alert_once_then_recover(_clean):
    sent = _clean
    assert not maintenance.active()
    maintenance.activate("API credit exhausted: test", ValueError("secret detail"))
    assert maintenance.active()
    assert "secret" not in str(maintenance.status())
    assert len(sent) == 1 and "manutenzione" in sent[0][0] and "credit" in sent[0][1]
    st = maintenance.status()
    assert st["active"] and st["since"] and "credit" in st["reason"]
    maintenance.activate("API credit exhausted: test again")   # renewed within the hour: no new e-mail
    assert len(sent) == 1
    maintenance.recovered()
    assert not maintenance.active()
    assert len(sent) == 2 and "attivo" in sent[1][0]
    assert maintenance.status() == {"active": False, "since": None, "reason": None}
    maintenance.recovered()   # no incident: nothing happens
    assert len(sent) == 2


def test_hold_expires(monkeypatch, _clean):
    monkeypatch.setattr(maintenance, "MAINTENANCE_HOLD_SECONDS", 0)
    maintenance.activate("x")
    assert not maintenance.active()          # the model is tried again at once
    assert maintenance.status()["since"]     # but the incident is still open
