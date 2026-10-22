
from api import rag
from crawler import state as st


def test_cached_answer_expires(monkeypatch):
    rag._response_cache.clear()
    rag._cache_set("Ciao ", {"x": 1})
    assert rag._cache_get("ciao") == {"x": 1}
    stored_at, day, value = rag._response_cache[rag._cache_key("ciao")]
    rag._response_cache[rag._cache_key("ciao")] = (stored_at - 7 * 3600, day, value)
    assert rag._cache_get("ciao") is None


def test_cached_answer_is_not_reused_on_another_day():
    rag._response_cache.clear()
    rag._cache_set("a", 1)
    key = rag._cache_key("a")
    stored_at, _, value = rag._response_cache[key]
    rag._response_cache[key] = (stored_at, "2000-01-01", value)
    assert rag._cache_get("a") is None


def test_old_cache_files_are_removed_only_when_nobody_uses_them(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "CRAWL_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(st, "STATE_FILE", tmp_path / "cache" / "state.json")
    monkeypatch.setattr(st, "DOCUMENTS_DIR", tmp_path / "docs")
    (tmp_path / "docs").mkdir(parents=True)
    state = st.CrawlState()
    old, shared, new = (tmp_path / "docs" / n for n in ("old.pdf", "shared.pdf", "new.pdf"))
    for f in (old, shared, new):
        f.write_bytes(b"x")
    state.update("u1", "h", file=str(old))
    state.update("u2", "h", file=str(shared))
    state.update("u3", "h", file=str(shared))
    state.update("u1", "h2", file=str(new))
    state.update("u2", "h2", file=str(new))
    assert state.delete_orphans() == 1
    assert not old.exists() and shared.exists() and new.exists()


