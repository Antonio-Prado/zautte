import datetime as dt
import json

from api import conversations, datalog, mailer
from api.safefile import dump_line
from config.settings import now_local
from scripts import purge_logs as pl


def _ts(days_ago):
    return (now_local() - dt.timedelta(days=days_ago)).isoformat(timespec="seconds")


def _rows(path):
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").split("\n") if ln.strip()]


def test_feedback_texts_expire_before_the_row_and_marks_follow(tmp_path, monkeypatch):
    f = tmp_path / "feedback.jsonl"
    r = tmp_path / "resolved_negative.json"
    monkeypatch.setattr(pl, "FEEDBACK_FILE", f)
    monkeypatch.setattr(datalog, "RESOLVED_FILE", r)
    monkeypatch.setattr(pl, "RETENTION_FEEDBACK_DAYS", 365)
    monkeypatch.setattr(pl, "RETENTION_FEEDBACK_TEXT_DAYS", 30)
    old_noid = {"ts": _ts(40), "rating": -1, "question": "vecchia", "uid": "u1", "user": "Mario"}
    old = {"id": "aa", "ts": _ts(40), "rating": 1, "question": "q", "answer_preview": "a", "comment": "c",
           "urls": ["https://x"], "uid": "u1", "rid": "r1"}
    fresh = {"id": "bb", "ts": _ts(3), "rating": -1, "question": "nuova", "uid": "u2"}
    gone = {"id": "cc", "ts": _ts(400), "rating": 1, "question": "antica", "uid": "u3"}
    f.write_text("".join(dump_line(e) + "\n" for e in (old_noid, old, fresh, gone)), encoding="utf-8")
    r.write_text(json.dumps([{"key": f"{old_noid['ts']}::vecchia"}, {"key": "id:bb"}]), encoding="utf-8")

    removed, stripped, _ = pl.purge_feedback(dry_run=False)
    rows = _rows(f)
    assert (removed, stripped) == (1, 2)
    assert rows[0] == {"ts": old_noid["ts"], "rating": -1, "uid": "u1"}
    assert rows[1] == {"id": "aa", "ts": old["ts"], "rating": 1, "uid": "u1", "rid": "r1"}
    assert rows[2] == fresh
    keys = {m["key"] for m in json.loads(r.read_text(encoding="utf-8"))}
    assert keys == {f"{old_noid['ts']}::", "id:bb"}   # the mark of the stripped row still matches it


def test_gaps_never_outlive_the_question_text(tmp_path, monkeypatch):
    g = tmp_path / "gaps.jsonl"
    monkeypatch.setattr(pl, "GAPS_FILE", g)
    monkeypatch.setattr(pl, "RETENTION_GAPS_DAYS", 180)
    monkeypatch.setattr(pl, "RETENTION_USAGE_TEXT_DAYS", 30)
    g.write_text(dump_line({"ts": _ts(40), "query": "x"}) + "\n" + dump_line({"ts": _ts(10), "query": "y"}) + "\n")
    assert pl.purge_gaps(dry_run=False) == 1
    assert [e["query"] for e in _rows(g)] == ["y"]


def test_notification_subject_carries_neither_name_nor_question():
    subject, body = mailer.build_feedback_notification(
        {"user": "Mario Rossi", "question": "Come pago la TARI?", "ts": "2026-10-22T10:00:00"})
    assert "Mario" not in subject and "TARI" not in subject
    assert "Come pago la TARI?" in body
    subject, _ = mailer.build_feedback_resolved({"question": "Come pago la TARI?"}, name="Mario")
    assert "TARI" not in subject


def test_conversation_memory_keeps_a_masked_answer():
    conversations._store.clear()
    conversations.add_turn("u", "c", "domanda", "Il tuo codice RSSMRA80A01H501Z è registrato")
    turns = conversations.history("u", "c") if hasattr(conversations, "history") else conversations._store[("u", "c")][1]
    assert "RSSMRA80A01H501Z" not in json.dumps(turns)
