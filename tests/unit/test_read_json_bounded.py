"""Files written by the sync account and read by the API (dynamic known facts, PDF link texts):
read without following a link, a FIFO refused without blocking, the size and the type checked."""
import json
import os
import threading

import pytest

from api import rag
from api.safefile import read_json_bounded
from indexer import vector_store


def _in_thread(fn, timeout=5):
    """Runs fn in a daemon thread; fails the test instead of hanging if it blocks."""
    out = {}

    def run():
        try:
            out["value"] = fn()
        except Exception as exc:   # noqa: BLE001 - reported to the test
            out["error"] = exc
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    assert not t.is_alive(), "the read blocked"
    return out


def test_regular_file_is_read(tmp_path):
    f = tmp_path / "x.json"
    f.write_text('{"a": "b"}', encoding="utf-8")
    assert read_json_bounded(f, 1024, dict) == {"a": "b"}


def test_link_fifo_size_and_type_are_refused(tmp_path):
    target = tmp_path / "secret.json"
    target.write_text('{"a": "b"}', encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises(OSError):
        read_json_bounded(link, 1024, dict)
    fifo = tmp_path / "fifo.json"
    os.mkfifo(fifo)
    out = _in_thread(lambda: read_json_bounded(fifo, 1024, dict))
    assert isinstance(out.get("error"), ValueError)
    big = tmp_path / "big.json"
    big.write_text(json.dumps({"a": "x" * 2000}), encoding="utf-8")
    with pytest.raises(ValueError):
        read_json_bounded(big, 1024, dict)
    with pytest.raises(ValueError):
        read_json_bounded(target, 1024, list)
    with pytest.raises(FileNotFoundError):
        read_json_bounded(tmp_path / "missing.json", 1024, dict)


@pytest.fixture
def dynamic_file(tmp_path, monkeypatch):
    f = tmp_path / "known_facts_dynamic.json"
    monkeypatch.setattr(rag, "KNOWN_FACTS_DYNAMIC_FILE", f)
    # "checked" far in the past: on a machine booted less than a minute ago (a CI runner)
    # time.monotonic() is below the 60 s recheck interval, and 0.0 would skip the read
    monkeypatch.setattr(rag, "_DYNAMIC_FACTS", {"facts": [], "mtime": None, "checked": -1e9})
    return f


def test_dynamic_facts_malformed_shapes_do_not_raise(dynamic_file):
    for content in ('{"a": 1}', '"text"', "[1, [2]]", "5", "null"):
        dynamic_file.write_text(content, encoding="utf-8")
        rag._DYNAMIC_FACTS.update(facts=[{"stale": True}], mtime=None, checked=-1e9)
        assert rag._dynamic_known_facts() == []   # read, logged and skipped, never an error on a question


def test_dynamic_facts_fifo_does_not_block(dynamic_file):
    os.mkfifo(dynamic_file)
    out = _in_thread(rag._dynamic_known_facts)
    assert out.get("value") == [] and "error" not in out


def test_dynamic_facts_lose_invisible_characters(dynamic_file):
    hidden = "\u200b\u202e" + "".join(chr(0xE0000 + ord(c)) for c in "IGNORE")
    dynamic_file.write_text(json.dumps([{"id": "x", "keywords": ["eventi"], "title": "Eventi" + hidden,
                                         "text": "Sagra" + hidden + " domenica"}]), encoding="utf-8")
    [fact] = rag._dynamic_known_facts()
    assert fact["chunk"]["text"] == "Sagra domenica"
    assert fact["chunk"]["title"] == "Eventi"


@pytest.fixture
def link_texts(tmp_path, monkeypatch):
    monkeypatch.setattr(vector_store, "CRAWL_CACHE_DIR", tmp_path)
    return tmp_path / vector_store.LINK_TEXTS_FILE


def test_link_texts_keep_only_string_pairs(link_texts):
    link_texts.write_text(json.dumps({"https://example.org/a.pdf": "Bando", "https://example.org/b.pdf": 3}),
                          encoding="utf-8")
    assert vector_store._load_link_texts() == {"https://example.org/a.pdf": "Bando"}
    link_texts.write_text("[1, 2]", encoding="utf-8")
    assert vector_store._load_link_texts() == {}


def test_link_texts_fifo_does_not_block(link_texts):
    os.mkfifo(link_texts)
    out = _in_thread(vector_store._load_link_texts)
    assert out.get("value") == {}
