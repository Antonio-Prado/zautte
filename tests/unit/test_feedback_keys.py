from api import feedback_store as fs


def test_new_feedback_resolved_by_id_not_by_time_and_question():
    a = {"id": "aa", "ts": "2026-10-22T10:00:00", "question": "q"}
    b = {"id": "bb", "ts": "2026-10-22T10:00:00", "question": "q"}
    resolved = {fs.entry_key(a)}
    assert fs.is_resolved(a, resolved)
    assert not fs.is_resolved(b, resolved)


def test_old_keys_still_count():
    e = {"id": "cc", "ts": "2026-09-20T10:00:00", "question": "old"}
    assert fs.is_resolved(e, {"2026-09-20T10:00:00::old"})


def test_entry_without_id_uses_the_old_key():
    e = {"ts": "2026-09-01T10:00:00", "question": "old"}
    assert fs.entry_key(e) == "2026-09-01T10:00:00::old"
