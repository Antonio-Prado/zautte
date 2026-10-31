"""GET /usage/messages pages through the log (most recent first, `offset` + `limit`) and can leave
out the retrieved passages, which the restricted area never shows and which are about half of the
payload: the panel reads the log from a phone too."""
import asyncio
import json

from api import main


def _write(tmp_path, n):
    usage = [{"ts": f"2026-10-27T10:{i:02d}:00+01:00", "uid": "u1", "q": f"domanda {i}", "rid": f"r{i}"}
             for i in range(n)]
    answers = [{"rid": f"r{i}", "a": f"risposta {i}", "sources": [{"title": "t", "url": "https://example.org"}],
                "retrieved": [{"title": "brano", "url": "https://example.org/b"}]} for i in range(n)]
    (tmp_path / "usage.jsonl").write_text("".join(json.dumps(e) + "\n" for e in usage), encoding="utf-8")
    (tmp_path / "answers.jsonl").write_text("".join(json.dumps(e) + "\n" for e in answers), encoding="utf-8")


def _get(**kw):
    return asyncio.run(main.usage_messages(_=None, **kw))


def test_default_is_unchanged_most_recent_first_with_retrieved(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "_DATA_DIR", tmp_path)
    _write(tmp_path, 5)
    d = _get(limit=300, offset=0, retrieved=True)
    assert d["total"] == 5
    assert [m["q"] for m in d["messages"]] == [f"domanda {i}" for i in (4, 3, 2, 1, 0)]
    assert d["messages"][0]["retrieved"] == [{"title": "brano", "url": "https://example.org/b"}]


def test_offset_reads_the_next_page_and_retrieved_can_be_left_out(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "_DATA_DIR", tmp_path)
    _write(tmp_path, 5)
    first = _get(limit=2, offset=0, retrieved=False)
    second = _get(limit=2, offset=2, retrieved=False)
    last = _get(limit=2, offset=4, retrieved=False)
    assert [m["rid"] for m in first["messages"]] == ["r4", "r3"]
    assert [m["rid"] for m in second["messages"]] == ["r2", "r1"]
    assert [m["rid"] for m in last["messages"]] == ["r0"]
    assert all("retrieved" not in m and m["a"] for m in first["messages"] + second["messages"])
    assert first["total"] == second["total"] == 5
    assert _get(limit=2, offset=9, retrieved=False)["messages"] == []
    assert _get(limit=2, offset=-3, retrieved=False)["messages"] == first["messages"]


def test_since_counts_the_messages_arrived_after_the_mark_without_test_accounts(tmp_path, monkeypatch):
    """0.17.0: the badge on the Messages tab asks `limit=0&since=<latest seen>` and reads `new`;
    `latest` is the server timestamp of the newest message, used as the next mark."""
    monkeypatch.setattr(main, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(main, "TEST_ACCOUNTS", {"zz-test"})
    _write(tmp_path, 4)
    with open(tmp_path / "usage.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": "2026-10-27T10:09:00+01:00", "uid": "zz-test", "q": "prova", "rid": "rt"}) + "\n")
    d = _get(limit=0, offset=0, retrieved=False, since="2026-10-27T10:01:00+01:00")
    assert d["messages"] == [] and d["total"] == 5
    assert d["latest"] == "2026-10-27T10:09:00+01:00"
    assert d["new"] == 2  # 10:02 and 10:03; the test account at 10:09 does not count
    page = _get(limit=2, offset=0, retrieved=False)["messages"]
    assert page[0]["uid"] == "zz-test" and page[0]["test"] is True  # 0.18.0: the panel skips it when highlighting
    assert "test" not in page[1]
    assert _get(limit=0, offset=0, retrieved=False, since="2026-10-27T08:30:00Z")["new"] == 4  # UTC mark (09:30 local): same instant rules
    assert _get(limit=0, offset=0, retrieved=False, since="")["new"] == 4  # unreadable mark: everything is new
    assert "new" not in _get(limit=1, offset=0, retrieved=False)


def test_seen_mark_and_highlighted_messages_live_on_the_server(tmp_path, monkeypatch):
    """0.19.0: the mark of the last look and the messages still highlighted as new are kept in
    data/messages_seen.json, so a phone and a PC of the administrator show the same marks."""
    monkeypatch.setattr(main, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(main, "TEST_ACCOUNTS", {"zz-test"})
    _write(tmp_path, 4)
    with open(tmp_path / "usage.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": "2026-10-27T10:09:00+01:00", "uid": "zz-test", "q": "prova", "rid": "rt"}) + "\n")
    first = _get(limit=0, offset=0, retrieved=False)
    assert first["seen"] == "" and first["fresh"] == [] and "new" not in first   # no mark yet: nothing is new

    # first look: the mark is set, nothing is highlighted (no backlog)
    st = asyncio.run(main.usage_messages_seen(main.MessagesSeenRequest(ts="2026-10-27T10:01:00+01:00"), _=None))
    assert st == {"seen": "2026-10-27T10:01:00+01:00", "fresh": []}
    d = _get(limit=0, offset=0, retrieved=False)
    assert d["seen"] == "2026-10-27T10:01:00+01:00" and d["new"] == 2   # 10:02 and 10:03, not the test account

    # second look (from any device): the messages arrived in between become fresh, the test account left out
    st = asyncio.run(main.usage_messages_seen(main.MessagesSeenRequest(ts=d["latest"]), _=None))
    assert st["seen"] == "2026-10-27T10:09:00+01:00"
    assert st["fresh"] == ["r2|2026-10-27T10:02:00+01:00|u1", "r3|2026-10-27T10:03:00+01:00|u1"]
    # an older mark does not move it back
    st = asyncio.run(main.usage_messages_seen(main.MessagesSeenRequest(ts="2026-10-27T10:00:00+01:00"), _=None))
    assert st["seen"] == "2026-10-27T10:09:00+01:00" and len(st["fresh"]) == 2

    # opened on one device: gone for the other one too
    r = asyncio.run(main.usage_message_opened(main.MessageOpenedRequest(key="r2|2026-10-27T10:02:00+01:00|u1"), _=None))
    assert r == {"fresh": ["r3|2026-10-27T10:03:00+01:00|u1"]}
    other = _get(limit=0, offset=0, retrieved=False)
    assert other["fresh"] == ["r3|2026-10-27T10:03:00+01:00|u1"] and other["new"] == 0
    assert json.loads((tmp_path / "messages_seen.json").read_text())["fresh"] == ["r3|2026-10-27T10:03:00+01:00|u1"]


def test_seen_mark_rejects_an_unreadable_timestamp(tmp_path, monkeypatch):
    import pytest
    from fastapi import HTTPException
    monkeypatch.setattr(main, "_DATA_DIR", tmp_path)
    with pytest.raises(HTTPException):
        asyncio.run(main.usage_messages_seen(main.MessagesSeenRequest(ts="ieri"), _=None))
    assert not (tmp_path / "messages_seen.json").exists()
