"""Hardening of the API (1 November review): startup refusals, limits on notification e-mails,
opportunistic STARTTLS, account lockout table, login queue, admin list limits, framing and SVG
headers, stats written off the event loop, embedding status in /health, sync account required."""
import asyncio
import json
import ssl
from typing import ClassVar

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from api import account_guard, admin_guard, auth, mailer, main, notify_guard, privdrop, rag
from config import settings

KEY = "k" * 32


def test_placeholder_admin_keys_are_refused(monkeypatch):
    for key in ("chiave-segreta-qui", "your-secret-key-here", "Your-Secret-Key-Here", "cambia-questa-chiave"):
        monkeypatch.setattr(admin_guard, "ADMIN_API_KEY", key)
        assert not admin_guard.admin_key_ok(), key
    monkeypatch.setattr(admin_guard, "ADMIN_API_KEY", KEY)
    assert admin_guard.admin_key_ok()


def test_admin_auth_disabled_only_on_loopback(monkeypatch):
    assert main._loopback_only("127.0.0.1,::1") and main._loopback_only("localhost")
    assert not main._loopback_only("0.0.0.0,::") and not main._loopback_only("127.0.0.1,192.0.2.5")
    monkeypatch.setattr(settings, "ADMIN_AUTH_DISABLED", True)
    monkeypatch.setattr(settings, "API_HOST", "0.0.0.0,::")
    monkeypatch.setattr(main, "AUTH_ENABLED", False)
    monkeypatch.setattr(main, "ADMIN_API_KEY", "")

    async def start():
        async with main.lifespan(main.app):
            pass
    with pytest.raises(RuntimeError, match="ADMIN_AUTH_DISABLED"):
        asyncio.run(start())


def test_feedback_and_application_notifications_are_limited(monkeypatch):
    monkeypatch.setattr(notify_guard, "_last_by_feedback", {})
    monkeypatch.setattr(notify_guard, "_sent", {"feedback": [], "application": []})
    monkeypatch.setattr(notify_guard, "FEEDBACK_NOTIFY_GAP_SECONDS", 600)
    monkeypatch.setattr(notify_guard, "FEEDBACK_NOTIFY_MAX_PER_HOUR", 3)
    monkeypatch.setattr(notify_guard, "APPLY_NOTIFY_MAX_PER_HOUR", 2)
    assert notify_guard.feedback_ok("a", now=1000)
    assert not notify_guard.feedback_ok("a", now=1100)        # same report within 10 minutes
    assert notify_guard.feedback_ok("a", now=1700)            # after the gap
    assert notify_guard.feedback_ok("b", now=1710)
    assert not notify_guard.feedback_ok("c", now=1720)        # 3 e-mails in the hour already
    assert notify_guard.feedback_ok("c", now=1000 + 3601)     # the first one left the hour
    assert notify_guard.application_ok(now=0) and notify_guard.application_ok(now=1)
    assert not notify_guard.application_ok(now=2)
    assert notify_guard.application_ok(now=3602)


class _FakeSMTP:
    instances: ClassVar[list] = []

    def __init__(self, host, port, timeout=None):
        self.calls = []
        self.extensions = {"starttls"}
        self.starttls_error = None
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ehlo(self):
        self.calls.append("ehlo")

    def has_extn(self, name):
        return name in self.extensions

    def starttls(self, context=None):
        self.calls.append("starttls")
        if self.starttls_error:
            raise self.starttls_error

    def login(self, *a):
        self.calls.append("login")

    def send_message(self, msg):
        self.calls.append("send")


@pytest.fixture
def relay(monkeypatch):
    _FakeSMTP.instances = []
    monkeypatch.setattr(mailer.smtplib, "SMTP", _FakeSMTP)
    for name, value in (("SMTP_HOST", "relay.example.org"), ("SMTP_FROM", "noreply@example.org"),
                        ("SMTP_USER", ""), ("SMTP_SSL", False), ("SMTP_STARTTLS", False), ("SMTP_PORT", 25)):
        monkeypatch.setattr(mailer, name, value)
    return _FakeSMTP


def test_starttls_used_when_the_relay_offers_it(relay):
    mailer.send_email("a@example.org", "Oggetto", "Testo")
    assert relay.instances[0].calls == ["ehlo", "starttls", "ehlo", "send"]


def test_failed_opportunistic_starttls_falls_back_to_a_plain_connection(relay, monkeypatch):
    orig_init = _FakeSMTP.__init__

    def init(self, *a, **k):
        orig_init(self, *a, **k)
        if len(_FakeSMTP.instances) == 1:
            self.starttls_error = ssl.SSLError("bad certificate")
    monkeypatch.setattr(_FakeSMTP, "__init__", init)
    mailer.send_email("a@example.org", "Oggetto", "Testo")
    first, second = relay.instances
    assert "send" not in first.calls and second.calls == ["ehlo", "send"]


def test_relay_without_starttls_sends_in_clear(relay, monkeypatch, caplog):
    monkeypatch.setattr(_FakeSMTP, "has_extn", lambda self, name: False)
    mailer.send_email("a@example.org", "Oggetto", "Testo")
    assert relay.instances[0].calls == ["ehlo", "send"]
    assert "does not offer STARTTLS" in caplog.text


def test_a_burst_of_other_addresses_does_not_erase_an_account_under_attack(monkeypatch):
    monkeypatch.setattr(account_guard, "_MAX_TRACKED", 10)
    monkeypatch.setattr(account_guard, "_failures", {})
    monkeypatch.setattr(account_guard, "LOGIN_MAX_FAILURES", 10)
    for _ in range(5):
        account_guard.login_failed("target@example.org")
    for i in range(50):
        account_guard.login_failed(f"noise{i}@example.org")
    assert len(account_guard._failures["target@example.org"]) == 5
    assert len(account_guard._failures) <= 10


def test_login_queue_has_a_bound(monkeypatch):
    monkeypatch.setattr(auth, "_password_queue", {"n": auth._PASSWORD_QUEUE_MAX})

    async def try_slot():
        async with auth._password_slot():
            pass
    with pytest.raises(HTTPException) as exc:
        asyncio.run(try_slot())
    assert exc.value.status_code == 503
    monkeypatch.setattr(auth, "_password_queue", {"n": 0})
    asyncio.run(try_slot())
    assert auth._password_queue["n"] == 0


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(admin_guard, "ADMIN_API_KEY", KEY)
    return TestClient(main.app)


def test_admin_list_limits_must_be_positive(client):
    for path in ("/gaps", "/feedback/list", "/feedback/negative"):
        assert client.get(path + "?limit=0", headers={"X-Admin-Key": KEY}).status_code == 422
        assert client.get(path + "?limit=-3", headers={"X-Admin-Key": KEY}).status_code == 422


def test_pages_cannot_be_framed_and_svg_runs_no_script(client):
    page = client.get("/widget/dashboard.html")
    assert page.status_code == 200 and page.headers.get("x-frame-options") == "DENY"
    svg = client.get("/widget/zautte-logo.svg")
    assert svg.status_code == 200
    assert "default-src 'none'" in svg.headers.get("content-security-policy", "")
    assert "sandbox" in svg.headers["content-security-policy"]


def test_admin_health_reports_the_embedding_state(client, monkeypatch):
    monkeypatch.setattr(main, "get_stats", lambda: {"total_chunks": 3, "unique_sources": 2, "doc_types": {}})
    monkeypatch.setattr(main, "get_top_doc", lambda: None)
    body = client.get("/health", headers={"X-Admin-Key": KEY}).json()
    assert body["indexed_chunks"] == 3 and "degraded" in body["embedding"]
    assert "names" not in body


def test_stats_are_written_off_the_loop_and_never_go_back(tmp_path, monkeypatch):
    f = tmp_path / "stats.json"
    monkeypatch.setattr(rag, "_STATS_FILE", f)

    async def two_saves():
        monkeypatch.setattr(rag, "_query_count", 1)
        rag._save_stats()
        monkeypatch.setattr(rag, "_query_count", 2)
        rag._save_stats()
        await asyncio.sleep(0.3)
    asyncio.run(two_saves())
    assert json.loads(f.read_text())["query_count"] == 2
    rag._write_stats(json.dumps({"query_count": 1}), rag._stats_seq - 1)   # an overtaken snapshot
    assert json.loads(f.read_text())["query_count"] == 2


def test_sync_jobs_refuse_root_without_the_sync_account(monkeypatch):
    monkeypatch.setattr(privdrop.os, "geteuid", lambda: 0)

    def missing(name):
        raise KeyError(name)
    monkeypatch.setattr(privdrop.pwd, "getpwnam", missing)
    monkeypatch.delenv("ALLOW_ROOT_SYNC", raising=False)
    with pytest.raises(SystemExit):
        privdrop.drop_to_sync_user()
    monkeypatch.setenv("ALLOW_ROOT_SYNC", "1")
    privdrop.drop_to_sync_user()   # runs as root anyway, with a warning
