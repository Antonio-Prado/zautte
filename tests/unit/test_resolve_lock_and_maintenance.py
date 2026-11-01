"""POST /feedback/resolve takes the file lock in the worker thread (holding it on the event loop
across an await let a second request deadlock the whole process), and a maintenance incident is
closed only by a real answer of the provider, not by a greeting or a cached answer."""
import asyncio
import json
import threading

from fastapi import BackgroundTasks
from starlette.requests import Request

from api import feedback_store as fs
from api import main, maintenance, rag

TS = "2026-10-25T20:00:00+02:00"


def test_resolve_never_takes_the_lock_on_the_event_loop(tmp_path, monkeypatch):
    f = tmp_path / "feedback.jsonl"
    f.write_text(json.dumps({"id": "abc123def456", "ts": TS, "question": "q", "rating": -1}) + "\n",
                 encoding="utf-8")
    monkeypatch.setattr(fs, "FEEDBACK_FILE", f)
    monkeypatch.setattr(main, "_resolved_negative_file", lambda: tmp_path / "resolved_negative.json")
    on_loop = []
    real_lock = main.file_lock

    def watching_lock(path):
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        return real_lock(path)
    monkeypatch.setattr(main, "file_lock", watching_lock)

    async def two_at_once():
        reqs = [main.ResolveRequest(id="abc123def456", ts=TS, question="q", notify=False),
                main.ResolveRequest(ts=TS, question="other", notify=False)]
        return await asyncio.gather(*(main.feedback_resolve(r, BackgroundTasks(), None) for r in reqs))
    # in a daemon thread: if the event loop deadlocks, the test fails instead of hanging
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("results", asyncio.run(two_at_once())), daemon=True)
    t.start()
    t.join(15)
    assert not t.is_alive(), "two concurrent resolves deadlocked the event loop"
    assert all(r["ok"] for r in out["results"])
    assert on_loop and not any(on_loop)


def test_a_greeting_does_not_end_maintenance(monkeypatch):
    """After the hold the incident is still open: a greeting through POST /chat is answered with
    a fixed text, no provider call, so it must not close the incident nor mail «back in service»."""
    monkeypatch.setattr(maintenance, "_state", {"until": 0.0, "since": 1.0, "reason": "API credit exhausted",
                                                "last_alert": 1.0})
    sent = []
    monkeypatch.setattr(maintenance, "_notify", lambda subject, body: sent.append(subject))
    monkeypatch.setattr(main, "_log_usage", lambda *a, **k: None)
    monkeypatch.setattr(main, "_log_answer", lambda *a, **k: None)
    request = Request({"type": "http", "method": "POST", "path": "/chat", "headers": [],
                       "client": ("192.0.2.1", 1234)})
    user = {"uid": "zz-maint-test", "name": "Prova"}
    result = asyncio.run(main.chat(request, main.ChatRequest(question="ciao"), user))
    assert result["answer"]
    assert maintenance.status()["since"] is not None and not sent


def test_a_provider_answer_ends_maintenance(monkeypatch):
    monkeypatch.setattr(maintenance, "_state", {"until": 0.0, "since": 1.0, "reason": "API credit exhausted",
                                                "last_alert": 1.0})
    sent = []
    monkeypatch.setattr(maintenance, "_notify", lambda subject, body: sent.append(subject))

    class _Resp:
        usage = None
        stop_reason = "end_turn"
        content = ()

    class _Messages:
        async def create(self, **kwargs):
            return _Resp()

    class _Client:
        messages = _Messages()
    monkeypatch.setattr(rag, "_claude_client", lambda: _Client())
    monkeypatch.setattr(rag, "_text_of", lambda response: "risposta")
    assert asyncio.run(rag.generate_claude([{"role": "user", "content": "x"}])) == "risposta"
    assert maintenance.status()["since"] is None and sent == ["Zautte — servizio di nuovo attivo"]
