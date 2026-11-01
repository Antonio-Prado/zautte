"""Hardening of ingestion and of the Ollama paths (1 November review): more hidden-text patterns,
only http(s) links in dynamic facts, one should_skip rule, a bounded Ollama answer, and the
question embedding that fails fast and is skipped for a while after a failure."""
import asyncio

import httpx
import pytest
from bs4 import BeautifulSoup

from api import rag
from crawler import crawler
from indexer import embedder
from scripts import council_sessions as cs
from scripts import events_calendar as ev


def _text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    crawler.drop_invisible(soup)
    return " ".join(soup.get_text(" ").split())


def test_more_ways_of_hiding_text_are_dropped():
    hidden = [
        '<span style="position:absolute;clip:rect(1px,1px,1px,1px)">H1</span>',
        '<span style="opacity:0.05">H2</span>', '<span style="opacity: .1">H3</span>',
        '<span style="font-size:3px">H4</span>', '<span style="font-size:.2em">H5</span>',
        '<span style="transform:scale(0)">H6</span>', '<span style="color:transparent">H7</span>',
        "<template><p>H8</p></template>",
    ]
    visible = [
        '<span style="background-color:transparent">V1</span>', '<span style="opacity:0.9">V2</span>',
        '<span style="font-size:0.8em">V3</span>', '<span style="font-size:14px">V4</span>',
        '<h2 style="color:transparent;background-clip:text;-webkit-background-clip:text">V5</h2>',
        '<span style="border-color: transparent">V6</span>',
    ]
    text = _text("<body>" + "".join(hidden + visible) + "</body>")
    for i in range(1, 9):
        assert f"H{i}" not in text
    for i in range(1, 7):
        assert f"V{i}" in text


def test_council_fact_never_keeps_a_javascript_link():
    page = ('<p>Convocazioni anno 2026</p><a href="javascript:alert(1)">O.d.G. Consiglio comunale del 12/11/2026</a>'
            '<a href="https://files.example.org/odg.doc">O.d.G. Consiglio comunale del 24/10/2026</a>')
    base = "https://www.example.org/convocazioni"
    urls = {s["date"].isoformat(): s["url"] for s in cs.parse_sessions(page, base)}
    assert urls["2026-11-12"] == base
    assert urls["2026-10-24"] == "https://files.example.org/odg.doc"


def test_event_links_must_be_http():
    assert ev._http_url("https://turismo.example.org/evento/x/") == "https://turismo.example.org/evento/x/"
    for bad in ("javascript:alert(1)", "data:text/html,x", "", None, "https://x.org/a b", "ftp://x.org/f"):
        assert ev._http_url(bad) == ""


def test_cleanup_uses_the_crawler_rule():
    from scripts import cleanup_index
    assert cleanup_index.should_skip is crawler.should_skip


class _SlowClient:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, *a, **k):
        await asyncio.sleep(5)


def test_ollama_answer_has_a_deadline(monkeypatch):
    monkeypatch.setattr(rag, "OLLAMA_CHAT_TIMEOUT", 0.2)
    monkeypatch.setattr(rag.httpx, "AsyncClient", _SlowClient)
    with pytest.raises(TimeoutError):
        asyncio.run(rag.generate_ollama([{"role": "user", "content": "x"}]))


class _Resp:
    def __init__(self, status=200, vec=None):
        self.status_code = status
        self._vec = vec

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("x", request=httpx.Request("POST", "http://o"), response=None)

    def json(self):
        return {"embeddings": [self._vec]}


class _EmbedClient:
    calls = 0
    mode = "down"

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, *a, **k):
        _EmbedClient.calls += 1
        if _EmbedClient.mode == "down":
            raise httpx.ConnectError("refused")
        return _Resp(vec=[0.1] * 4)


def test_question_embedding_fails_fast_and_is_skipped_for_a_while(monkeypatch):
    monkeypatch.setattr(embedder.httpx, "Client", _EmbedClient)
    monkeypatch.setattr(embedder, "EMBEDDING_DIMENSION", 4)
    monkeypatch.setattr(embedder, "_query_state", {"failures": 0, "down_until": 0.0, "last_error": "",
                                                   "last_failure": None, "skipped": 0})
    _EmbedClient.calls, _EmbedClient.mode = 0, "down"
    assert embedder.embed_query("carta d'identità") == [0.0] * 4
    assert _EmbedClient.calls == 1                      # one attempt, no retry chain
    assert embedder.embed_query("tari") == [0.0] * 4    # within the cooldown: Ollama not called
    assert _EmbedClient.calls == 1
    status = embedder.query_embedding_status()
    assert status["degraded"] and status["consecutive_failures"] == 1 and status["skipped_since_failure"] == 1
    embedder._query_state["down_until"] = 0.0           # the cooldown is over
    _EmbedClient.mode = "up"
    assert embedder.embed_query("tari") == [0.1] * 4
    assert not embedder.query_embedding_status()["degraded"]
    assert embedder.query_embedding_status()["consecutive_failures"] == 0
