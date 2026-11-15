"""Links written inside the passages: the dead ones are replaced before the model sees them."""
import asyncio

import httpx
import pytest

from api import linkcheck

STATUS = {
    "https://www.example.org/gone": 410,
    "https://www.example.org/missing": 404,
    "https://www.example.org/ok": 200,
    "https://www.example.org/forbidden": 403,
}


@pytest.fixture(autouse=True)
def fake_site(monkeypatch):
    calls = []

    async def status(address):
        calls.append(address)
        if address.endswith("/slow"):
            await asyncio.sleep(0.3)
            return 404
        if address.endswith("/down"):
            raise httpx.ConnectError("down")
        return STATUS[address]

    linkcheck._cache.clear()
    monkeypatch.setattr(linkcheck, "_status", status)
    monkeypatch.setattr(linkcheck, "LINK_CHECK", True)
    monkeypatch.setattr(linkcheck, "LINK_CHECK_SECONDS", 0.1)
    return calls


def clean(text):
    return asyncio.run(linkcheck.without_dead_links([{"text": text}]))[0]["text"]


def test_dead_links_are_replaced_and_live_ones_kept():
    text = ("Il servizio è su https://www.example.org/ok. Le guide: https://www.example.org/gone, "
            "il modulo (https://www.example.org/missing).")
    assert clean(text) == (f"Il servizio è su https://www.example.org/ok. Le guide: {linkcheck.DEAD_LINK_NOTE}, "
                           f"il modulo ({linkcheck.DEAD_LINK_NOTE}).")


def test_bare_www_link():
    assert clean("vedi www.example.org/gone per i filmati") == f"vedi {linkcheck.DEAD_LINK_NOTE} per i filmati"


def test_refusals_and_errors_leave_the_link():
    text = "https://www.example.org/forbidden e https://www.example.org/down"
    assert clean(text) == text


def test_other_sites_are_never_called(fake_site):
    text = "http://127.0.0.1:11434/api e https://evil.example.net/x e https://user:pw@example.org/gone"
    assert clean(text) == text
    assert fake_site == []


def test_result_is_remembered(fake_site):
    clean("https://www.example.org/gone")
    clean("https://www.example.org/gone")
    assert fake_site == ["https://www.example.org/gone"]


def test_slow_check_does_not_hold_the_question_and_serves_the_next():
    async def run():
        chunks = [{"text": "https://www.example.org/slow"}]
        first = await linkcheck.without_dead_links(chunks)
        await asyncio.sleep(0.4)
        return first[0]["text"], (await linkcheck.without_dead_links(chunks))[0]["text"]

    first, second = asyncio.run(run())
    assert first == "https://www.example.org/slow"
    assert second == linkcheck.DEAD_LINK_NOTE


def test_passages_are_not_modified_in_place():
    chunk = {"text": "https://www.example.org/gone", "title": "t"}
    out = asyncio.run(linkcheck.without_dead_links([chunk]))
    assert chunk["text"] == "https://www.example.org/gone" and out[0]["title"] == "t"


def test_switched_off(monkeypatch, fake_site):
    monkeypatch.setattr(linkcheck, "LINK_CHECK", False)
    assert clean("https://www.example.org/gone") == "https://www.example.org/gone"
    assert fake_site == []
