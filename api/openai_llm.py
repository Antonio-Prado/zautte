"""
Chat model behind an OpenAI-compatible API (LLM_PROVIDER=openai): any service that speaks
`POST {OPENAI_BASE_URL}/chat/completions`, hosted or on the organisation's own machines.

Only the transport lives here: one complete answer (chat) or one streamed answer (chat_stream),
with the token counts the provider reports. What to do with them (cost history, maintenance
mode, refusals) is decided by the callers in api/rag.py, as for the other providers.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator

import httpx

from config.settings import (
    OPENAI_ANSWER_NOTE,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_REASONING_EFFORT,
    OPENAI_TIMEOUT,
)

log = logging.getLogger(__name__)

# Connecting and each wait for data are bounded; the whole answer within OPENAI_TIMEOUT.
_HTTP_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0)
_RETRY_WAIT_SECONDS = 2.0


class ProviderError(RuntimeError):
    """The provider answered with an HTTP error. The text carries the status only: the body, which
    may name the account or the plan, goes to the log and to `detail`, never to the visitor."""

    def __init__(self, status_code: int, detail: str = ""):
        super().__init__(f"the model provider answered HTTP {status_code}")
        self.status_code = status_code
        self.detail = detail


def with_answer_note(messages: list[dict]) -> list[dict]:
    """The messages of an answer with OPENAI_ANSWER_NOTE after the question (the last user
    message); the same list when no note is set. The messages passed in are left untouched: they
    are also the ones of the model in service, when this one answers in the shadow."""
    if not OPENAI_ANSWER_NOTE or not messages or messages[-1].get("role") != "user":
        return messages
    last = dict(messages[-1])
    last["content"] = f'{last["content"]}\n\n{OPENAI_ANSWER_NOTE}'
    return [*messages[:-1], last]


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=_HTTP_TIMEOUT)


def _url() -> str:
    return OPENAI_BASE_URL.rstrip("/") + "/chat/completions"


def _headers() -> dict:
    headers = {"Content-Type": "application/json"}
    if OPENAI_API_KEY:
        headers["Authorization"] = f"Bearer {OPENAI_API_KEY}"
    return headers


def _payload(messages: list[dict], model: str, temperature: float, max_tokens: int, stream: bool) -> dict:
    payload: dict = {"model": model, "messages": messages, "temperature": temperature,
                     "max_tokens": max_tokens, "stream": stream}
    if stream:
        payload["stream_options"] = {"include_usage": True}   # token counts in the last event
    if OPENAI_REASONING_EFFORT:
        # models that reason by default: "none" turns the reasoning off where the provider allows it
        payload["reasoning_effort"] = OPENAI_REASONING_EFFORT
    return payload


def _usage(data: dict) -> dict | None:
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return None
    return {"in": int(usage.get("prompt_tokens") or 0), "out": int(usage.get("completion_tokens") or 0)}


def _raise_for(status: int, body: str) -> None:
    if status >= 400:
        log.warning("Model provider HTTP %d: %s", status, body[:300])
        raise ProviderError(status, body[:300])


def _retryable(exc: Exception) -> bool:
    """Worth one more try: the provider's own errors and network trouble, not a refused request."""
    if isinstance(exc, ProviderError):
        return exc.status_code >= 500
    return isinstance(exc, httpx.TransportError)


async def chat(messages: list[dict], model: str, temperature: float, max_tokens: int) -> dict:
    """One complete answer: {"text", "usage": {"in", "out"} or None, "finish": finish_reason}.
    Retries once on a 5xx or a network error."""
    payload = _payload(messages, model, temperature, max_tokens, stream=False)
    for attempt in range(2):
        try:
            async with asyncio.timeout(OPENAI_TIMEOUT), _client() as client:
                resp = await client.post(_url(), json=payload, headers=_headers())
                _raise_for(resp.status_code, resp.text)
                data = resp.json()
            choice = (data.get("choices") or [{}])[0]
            return {"text": ((choice.get("message") or {}).get("content") or "").strip(),
                    "usage": _usage(data), "finish": choice.get("finish_reason")}
        except Exception as exc:   # re-raised unless it is worth one more try
            if attempt or not _retryable(exc):
                raise
            log.warning("Model provider: %s, trying once more", exc)
            await asyncio.sleep(_RETRY_WAIT_SECONDS)
    raise AssertionError("unreachable")


async def chat_stream(messages: list[dict], model: str, temperature: float, max_tokens: int,
                      result: dict) -> AsyncGenerator[str, None]:
    """One streamed answer: yields the text as it arrives; at the end `result` holds "usage"
    ({"in", "out"}, when the provider reports it) and "finish" (finish_reason). The reasoning of
    the models that reason, sent as a separate field, is not part of the answer. Retries once on a
    5xx or a network error, only while nothing has been yielded yet."""
    payload = _payload(messages, model, temperature, max_tokens, stream=True)
    # A deadline checked at each line, not asyncio.timeout(): a generator may be resumed and
    # closed by different tasks, which a timeout scope does not allow.
    deadline = asyncio.get_running_loop().time() + OPENAI_TIMEOUT
    for attempt in range(2):
        sent = False
        try:
            async with _client() as client, \
                    client.stream("POST", _url(), json=payload, headers=_headers()) as resp:
                if resp.status_code >= 400:
                    _raise_for(resp.status_code, (await resp.aread()).decode("utf-8", "replace"))
                async for line in resp.aiter_lines():
                    if asyncio.get_running_loop().time() > deadline:
                        log.warning("Model provider: answer stopped after %d s", OPENAI_TIMEOUT)
                        result["finish"] = "timeout"
                        return
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    usage = _usage(event)
                    if usage:
                        result["usage"] = usage
                    for choice in event.get("choices") or []:
                        text = (choice.get("delta") or {}).get("content")
                        if text:
                            sent = True
                            yield text
                        if choice.get("finish_reason"):
                            result["finish"] = choice["finish_reason"]
            return
        except Exception as exc:   # re-raised unless it is worth one more try
            if attempt or sent or not _retryable(exc):
                raise
            log.warning("Model provider: %s, trying once more", exc)
            await asyncio.sleep(_RETRY_WAIT_SECONDS)
