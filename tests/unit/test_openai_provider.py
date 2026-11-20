"""LLM_PROVIDER=openai: answers, streamed answers and question rewrites through an OpenAI-compatible
API, with the token counts recorded and the provider's refusals turned into maintenance mode."""
import asyncio
import json

import httpx
import pytest

from api import maintenance, openai_llm, rag

MESSAGES = [{"role": "system", "content": "rules"}, {"role": "user", "content": "DOMANDA: orari?"}]


def _sse(*events):
    return "".join(f"data: {e if isinstance(e, str) else json.dumps(e)}\n\n" for e in events)


@pytest.fixture
def provider(monkeypatch):
    """A fake provider: `calls` collects the requests, `answer` is what it replies (a callable
    taking the request number, or a fixed httpx.Response factory)."""
    state = {"calls": [], "answer": None}

    def handler(request):
        state["calls"].append({"url": str(request.url), "auth": request.headers.get("authorization"),
                               "body": json.loads(request.content)})
        return state["answer"](len(state["calls"]))

    monkeypatch.setattr(openai_llm, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(openai_llm, "OPENAI_BASE_URL", "https://llm.example.org/v1/")
    monkeypatch.setattr(openai_llm, "OPENAI_API_KEY", "k-test")
    monkeypatch.setattr(openai_llm, "OPENAI_REASONING_EFFORT", "none")
    monkeypatch.setattr(openai_llm, "_RETRY_WAIT_SECONDS", 0)
    monkeypatch.setattr(rag, "OPENAI_MODEL", "model-a")
    monkeypatch.setattr(rag, "OPENAI_REWRITE_MODEL", "model-small")
    recorded = []
    monkeypatch.setattr(rag, "_record_tokens", lambda i, o, model, uid=None: recorded.append((i, o, model, uid)))
    monkeypatch.setattr(maintenance, "recovered", lambda: recorded.append("recovered"))
    state["recorded"] = recorded
    return state


def _complete(text, finish="stop"):
    return lambda n: httpx.Response(200, json={
        "choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": finish}],
        "usage": {"prompt_tokens": 6500, "completion_tokens": 300}})


def test_complete_answer_request_and_tokens(provider):
    provider["answer"] = _complete("  L'ufficio apre alle 9.  ")
    assert asyncio.run(rag.generate_openai(MESSAGES, uid="u1")) == "L'ufficio apre alle 9."
    call = provider["calls"][0]
    assert call["url"] == "https://llm.example.org/v1/chat/completions"
    assert call["auth"] == "Bearer k-test"
    assert call["body"]["model"] == "model-a" and call["body"]["messages"] == MESSAGES
    assert call["body"]["stream"] is False and call["body"]["reasoning_effort"] == "none"
    assert call["body"]["max_tokens"] == 1024 and call["body"]["temperature"] == 0.3
    assert provider["recorded"] == ["recovered", (6500, 300, "model-a", "u1")]


def test_reasoning_effort_is_sent_only_when_set(provider, monkeypatch):
    monkeypatch.setattr(openai_llm, "OPENAI_REASONING_EFFORT", "")
    provider["answer"] = _complete("ok")
    asyncio.run(rag.generate_openai(MESSAGES))
    assert "reasoning_effort" not in provider["calls"][0]["body"]


def test_streamed_answer_leaves_the_reasoning_out(provider):
    provider["answer"] = lambda n: httpx.Response(200, text=_sse(
        {"choices": [{"delta": {"role": "assistant", "reasoning_content": "let me think"}}]},
        {"choices": [{"delta": {"content": "L'ufficio "}}]},
        "not json",
        {"choices": [{"delta": {"content": "apre alle 9."}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 7000, "completion_tokens": 12}},
        "[DONE]"))

    async def run():
        return [t async for t in rag.stream_openai(MESSAGES, uid="u2")]

    assert asyncio.run(run()) == ["L'ufficio ", "apre alle 9."]
    body = provider["calls"][0]["body"]
    assert body["stream"] is True and body["stream_options"] == {"include_usage": True}
    assert provider["recorded"] == ["recovered", (7000, 12, "model-a", "u2")]


def test_content_filter_adds_the_refusal_text(provider):
    provider["answer"] = _complete("", finish="content_filter")
    assert asyncio.run(rag.generate_openai(MESSAGES)) == rag._REFUSAL_TEXT
    provider["answer"] = lambda n: httpx.Response(200, text=_sse(
        {"choices": [{"delta": {"content": "Non"}, "finish_reason": "content_filter"}]}, "[DONE]"))

    async def run():
        return "".join([t async for t in rag.stream_openai(MESSAGES)])

    assert asyncio.run(run()) == "Non\n\n" + rag._REFUSAL_TEXT


def test_rewrite_uses_the_rewrite_model(provider):
    provider["answer"] = _complete("Quanto costa il passo carrabile?")
    assert asyncio.run(rag._rewrite_openai(MESSAGES, uid="u3")) == "Quanto costa il passo carrabile?"
    body = provider["calls"][0]["body"]
    assert body["model"] == "model-small" and body["temperature"] == 0.0 and body["max_tokens"] == 200
    assert provider["recorded"] == [(6500, 300, "model-small", "u3")]


@pytest.mark.parametrize(("status", "reason"), [
    (401, "not valid"), (402, "402"), (403, "permission")])
def test_refusals_to_serve_start_the_maintenance_mode(provider, status, reason):
    provider["answer"] = lambda n: httpx.Response(status, json={"error": {"message": "account acme: daily_limit"}})
    with pytest.raises(openai_llm.ProviderError) as err:
        asyncio.run(rag.generate_openai(MESSAGES))
    assert "acme" not in str(err.value)            # the provider's message never reaches the visitor
    assert reason in maintenance.classify(err.value)
    assert len(provider["calls"]) == 1             # a refused request is not tried again
    assert provider["recorded"] == []


@pytest.mark.parametrize("status", [400, 404, 429, 500])
def test_ordinary_errors_are_not_maintenance(provider, status):
    provider["answer"] = lambda n: httpx.Response(status, text="boom")
    with pytest.raises(openai_llm.ProviderError) as err:
        asyncio.run(rag.generate_openai(MESSAGES))
    assert maintenance.classify(err.value) is None


def test_a_server_error_is_tried_once_more(provider):
    provider["answer"] = lambda n: httpx.Response(503, text="busy") if n == 1 else _complete("ok")(n)
    assert asyncio.run(rag.generate_openai(MESSAGES)) == "ok"
    assert len(provider["calls"]) == 2

    provider["calls"].clear()
    provider["answer"] = lambda n: (httpx.Response(502, text="bad gateway") if n == 1 else httpx.Response(
        200, text=_sse({"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}, "[DONE]")))

    async def run():
        return [t async for t in rag.stream_openai(MESSAGES)]

    assert asyncio.run(run()) == ["ok"]
    assert len(provider["calls"]) == 2


def test_cost_from_the_installation_price_list(monkeypatch):
    monkeypatch.setattr(rag, "_USES_OPENAI", True)
    monkeypatch.setattr(rag, "OPENAI_PRICE_IN", 0.45)
    monkeypatch.setattr(rag, "OPENAI_PRICE_OUT", 1.80)
    assert rag._cost_usd(1_000_000, 500_000, "any-model") == pytest.approx(0.45 + 0.90)
    monkeypatch.setattr(rag, "_USES_OPENAI", False)
    assert rag._cost_usd(1_000_000, 0, "claude-sonnet-4-6") == pytest.approx(3.0)
