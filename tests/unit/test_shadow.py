"""Shadow answers: the model under comparison gets the same messages in the background, its
answer is logged (never shown), timed against the answer in service, and forgotten on request."""
import asyncio
import json

import httpx
import pytest

from api import datalog, openai_llm, shadow

MESSAGES = [{"role": "system", "content": "rules"}, {"role": "user", "content": "DOMANDA: orari?"}]


def _sse(*events):
    return "".join(f"data: {e if isinstance(e, str) else json.dumps(e)}\n\n" for e in events)


@pytest.fixture
def on(monkeypatch, tmp_path):
    """Shadow answers on, a fake second model, the log in a temporary file."""
    log_file = tmp_path / "shadow.jsonl"
    state = {"calls": [], "reply": lambda: httpx.Response(200, text=_sse(
        {"choices": [{"delta": {"content": "Chiama il 0735 000000 "}}]},
        {"choices": [{"delta": {"content": "o scrivi a mario.rossi@example.com."}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 6000, "completion_tokens": 200}}, "[DONE]"))}

    def handler(request):
        state["calls"].append(json.loads(request.content))
        return state["reply"]()

    monkeypatch.setattr(openai_llm, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(openai_llm, "OPENAI_BASE_URL", "https://llm.example.org/v1")
    monkeypatch.setattr(openai_llm, "_RETRY_WAIT_SECONDS", 0)
    for name, value in (("SHADOW_OPENAI", True), ("LLM_PROVIDER", "claude"), ("OPENAI_BASE_URL", "https://llm.example.org/v1"),
                        ("OPENAI_MODEL", "model-b"), ("OPENAI_PRICE_IN", 0.5), ("OPENAI_PRICE_OUT", 2.0),
                        ("SHADOW_FILE", log_file)):
        monkeypatch.setattr(shadow, name, value)
    state["entries"] = lambda: [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()] \
        if log_file.exists() else []
    return state


async def _drain():
    while shadow._running:
        await asyncio.gather(*list(shadow._running))


def test_on_only_with_a_second_model_and_another_one_in_service(on, monkeypatch):
    assert shadow.enabled()
    monkeypatch.setattr(shadow, "LLM_PROVIDER", "openai")     # the same model would answer twice
    assert not shadow.enabled()
    monkeypatch.setattr(shadow, "LLM_PROVIDER", "claude")
    monkeypatch.setattr(shadow, "OPENAI_MODEL", "")
    assert not shadow.enabled()
    monkeypatch.setattr(shadow, "OPENAI_MODEL", "model-b")
    monkeypatch.setattr(shadow, "SHADOW_OPENAI", False)
    assert not shadow.enabled()


def test_the_same_messages_go_to_the_second_model_and_the_answer_is_logged(on):
    async def run():
        shadow.start("rid000000001", "u1", MESSAGES, guard=lambda text: text.replace("0735 000000", "[numero rimosso]"))
        await _drain()

    asyncio.run(run())
    assert on["calls"][0]["messages"] == MESSAGES and on["calls"][0]["model"] == "model-b"
    (entry,) = on["entries"]()
    assert entry["kind"] == "shadow" and entry["rid"] == "rid000000001" and entry["uid"] == "u1"
    assert "[numero rimosso]" in entry["a"]                    # the guard of the answers shown to users
    assert "mario.rossi@example.com" not in entry["a"]         # masked like a logged answer
    assert entry["tokens_in"] == 6000 and entry["tokens_out"] == 200
    assert entry["cost"] == pytest.approx(6000 * 0.5 / 1e6 + 200 * 2.0 / 1e6)
    assert entry["total_ms"] >= entry["first_ms"] >= 0 and entry["finish"] == "stop"
    assert "error" not in entry


@pytest.mark.parametrize(("rid", "uid"), [(None, "u1"), ("rid000000002", None), ("rid000000002", "anon")])
def test_only_questions_of_logged_in_users(on, rid, uid):
    async def run():
        shadow.start(rid, uid, MESSAGES)
        await _drain()

    asyncio.run(run())
    assert on["calls"] == [] and on["entries"]() == []


def test_off_means_no_call(on, monkeypatch):
    monkeypatch.setattr(shadow, "SHADOW_OPENAI", False)

    async def run():
        shadow.start("rid000000003", "u1", MESSAGES)
        await _drain()
        return [t async for t in shadow.timed(_tokens("a", "b"), "rid000000003", "u1", "model-a")]

    assert asyncio.run(run()) == ["a", "b"]
    assert on["calls"] == [] and on["entries"]() == []


def test_a_failed_call_is_recorded_and_harmless(on):
    on["reply"] = lambda: httpx.Response(402, json={"error": {"message": "account acme: daily limit"}})

    async def run():
        shadow.start("rid000000004", "u1", MESSAGES)
        await _drain()

    asyncio.run(run())
    (entry,) = on["entries"]()
    assert entry["error"] == "the model provider answered HTTP 402" and entry["a"] == ""
    assert "acme" not in json.dumps(entry)


async def _tokens(*parts):
    for p in parts:
        yield p


def test_the_answer_in_service_is_timed_and_left_untouched(on):
    async def run():
        return [t async for t in shadow.timed(_tokens("L'ufficio ", "apre alle 9."), "rid000000005", "u1", "model-a")]

    assert asyncio.run(run()) == ["L'ufficio ", "apre alle 9."]
    (entry,) = on["entries"]()
    assert entry["kind"] == "primary" and entry["model"] == "model-a" and entry["rid"] == "rid000000005"
    assert entry["total_ms"] >= entry["first_ms"] >= 0 and "a" not in entry


def test_comparisons_join_by_question_and_summarise(on):
    log = [
        {"kind": "primary", "rid": "r1", "uid": "u1", "model": "model-a", "first_ms": 900, "total_ms": 9000},
        {"kind": "shadow", "rid": "r1", "uid": "u1", "ts": "2026-11-20T10:00:05", "model": "model-b", "a": "B1",
         "first_ms": 300, "total_ms": 3000, "tokens_in": 6000, "tokens_out": 200, "cost": 0.0034},
        {"kind": "primary", "rid": "r2", "uid": "u2", "model": "model-a", "first_ms": 1100, "total_ms": 4000},
        {"kind": "shadow", "rid": "r2", "uid": "u2", "ts": "2026-11-20T11:00:05", "model": "model-b", "a": "B2",
         "first_ms": 400, "total_ms": 5000, "cost": 0.004},
        {"kind": "shadow", "rid": "r3", "uid": "u1", "ts": "2026-11-20T12:00:05", "model": "model-b", "a": "",
         "total_ms": 800, "error": "the model provider answered HTTP 500"},
    ]
    usage = [{"rid": "r1", "ts": "2026-11-20T10:00:00", "q": "Q1"}, {"rid": "r2", "ts": "2026-11-20T11:00:00", "q": "Q2"}]
    answers = [{"rid": "r1", "a": "A1"}, {"rid": "r2", "a": "A2", "partial": True}]
    out = shadow.comparisons(log, usage, answers, {"u1": "Ada", "u2": "Bo"}, limit=2)
    assert [i["rid"] for i in out["items"]] == ["r3", "r2"]            # most recent first, one page
    r2 = out["items"][1]
    assert r2["user"] == "Bo" and r2["q"] == "Q2"
    assert r2["primary"] == {"model": "model-a", "a": "A2", "partial": True, "first_ms": 1100, "total_ms": 4000}
    assert r2["shadow"]["a"] == "B2" and r2["shadow"]["total_ms"] == 5000
    assert out["items"][0]["q"] == "" and out["items"][0]["shadow"]["error"]
    s = out["summary"]
    assert (s["total"], s["errors"], s["timed"], s["shadow_faster"]) == (3, 1, 2, 1)
    assert (s["primary_median_ms"], s["shadow_median_ms"]) == (6500, 4000)
    assert (s["primary_first_median_ms"], s["shadow_first_median_ms"]) == (1000, 350)
    assert s["shadow_cost"] == pytest.approx(0.0074) and s["shadow_model"] == "model-b" and s["enabled"]


def test_deletion_on_request_removes_the_shadow_answer(monkeypatch, tmp_path):
    files = {n: tmp_path / f"{n}.jsonl" for n in ("usage", "answers", "shadow", "gaps", "feedback")}
    for name, path in files.items():
        monkeypatch.setattr(datalog, f"{name.upper()}_FILE", path)
    monkeypatch.setattr(datalog, "RESOLVED_FILE", tmp_path / "resolved.json")
    files["usage"].write_text('{"ts": "2026-11-20T10:00:00", "uid": "u1", "rid": "r1", "q": "Q1"}\n'
                              '{"ts": "2026-11-20T11:00:00", "uid": "u1", "rid": "r2", "q": "Q2"}\n', encoding="utf-8")
    files["shadow"].write_text('{"kind": "primary", "rid": "r1", "uid": "u1"}\n{"kind": "shadow", "rid": "r1", "a": "B1"}\n'
                               '{"kind": "shadow", "rid": "r2", "a": "B2"}\n', encoding="utf-8")
    result = datalog.forget(rids=["r1"])
    assert result["shadow"] == 2
    assert [json.loads(line)["rid"] for line in files["shadow"].read_text(encoding="utf-8").splitlines()] == ["r2"]


def test_answer_sends_the_same_messages_and_shows_only_the_answer_in_service(on, monkeypatch):
    from api import rag

    seen = {}

    async def in_service(messages, uid=None):
        seen["messages"] = messages
        for part in ("Lo sportello ", "apre alle 9."):
            yield part

    async def no_dead_links(chunks):
        return chunks

    monkeypatch.setattr(rag, "retrieve_context", lambda q: [
        {"text": "Lo sportello apre alle 9.", "title": "Sportello", "source": "https://example.org/it/sportello",
         "score": 0.8}])
    monkeypatch.setattr(rag.linkcheck, "without_dead_links", no_dead_links)
    monkeypatch.setattr(rag, "_USES_CLAUDE", True)
    monkeypatch.setattr(rag, "_USES_OPENAI", False)
    monkeypatch.setattr(rag, "_ANSWER_MODEL", "model-a")
    monkeypatch.setattr(rag, "stream_claude", in_service)
    monkeypatch.setattr(rag, "_save_stats", lambda: None)
    monkeypatch.setattr(rag, "_log_gap", lambda *a, **k: None)

    async def run():
        gen, _sources, _language, _hosts = await rag.answer("Quando apre lo sportello?", stream=True,
                                                            uid="u1", rid="rid000000009")
        text = "".join([t async for t in gen])
        await _drain()
        return text

    assert asyncio.run(run()) == "Lo sportello apre alle 9."          # never the shadow answer
    assert on["calls"][0]["messages"] == seen["messages"]               # the very same prompt
    kinds = {e["kind"]: e for e in on["entries"]()}
    assert kinds["primary"]["model"] == "model-a" and kinds["primary"]["rid"] == "rid000000009"
    assert kinds["shadow"]["model"] == "model-b" and kinds["shadow"]["rid"] == "rid000000009"
