"""The resolution note is kept whole up to the limit checked on the request: until 0.10.5 the
record and the acknowledgement email were cut at 1000 characters although 2000 were accepted."""
import json

import pytest
from fastapi import BackgroundTasks
from pydantic import ValidationError

from api import auth, main
from api import feedback_store as fs

TS = "2026-10-25T20:00:00+02:00"
QUESTION = "L'atto notorio per la successione da chi deve essere fatto?"


@pytest.fixture
def resolved_file(tmp_path, monkeypatch):
    f = tmp_path / "feedback.jsonl"
    f.write_text(json.dumps({"id": "abc123def456", "ts": TS, "question": QUESTION, "rating": -1,
                             "uid": "u1", "user": "Laura"}) + "\n", encoding="utf-8")
    monkeypatch.setattr(fs, "FEEDBACK_FILE", f)
    monkeypatch.setattr(auth, "get_user_by_id",
                        lambda uid: {"id": uid, "name": "Laura", "email": "laura@example.org"})
    return tmp_path / "resolved_negative.json"


def test_long_note_is_kept_whole_in_the_record_and_the_email(resolved_file):
    note = "Grazie della segnalazione: da oggi Zautte risponde anche a questo. " * 22   # about 1500 characters
    assert 1000 < len(note) <= main._RESOLVE_NOTE_MAX
    req = main.ResolveRequest(id="abc123def456", ts=TS, question=QUESTION, note=note)
    tasks = BackgroundTasks()
    assert main._resolve_locked(resolved_file, req, tasks) == {"ok": True, "notified": True}
    record = json.loads(resolved_file.read_text(encoding="utf-8"))[0]
    assert record["key"] == "id:abc123def456"
    assert record["note"] == note.strip()
    to, _name, _entry, sent_note = tasks.tasks[0].args
    assert to == "laura@example.org"
    assert sent_note == note.strip()


def test_note_over_the_limit_is_refused():
    with pytest.raises(ValidationError):
        main.ResolveRequest(ts=TS, question=QUESTION, note="x" * (main._RESOLVE_NOTE_MAX + 1))
