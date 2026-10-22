import json

from api import datalog
from api.safefile import dump_line


def test_unicode_line_separators_stay_on_one_line_and_read_back():
    entry = {"q": "ciao mondo x\x85y", "uid": "u"}
    line = dump_line(entry)
    assert len(line.splitlines()) == 1
    assert json.loads(line) == entry


def test_rewrite_keeps_a_record_with_a_separator_whole(tmp_path):
    f = tmp_path / "usage.jsonl"
    f.write_text(dump_line({"ts": "2026-10-22T10:00:00", "q": "a b", "uid": "u"}) + "\n", encoding="utf-8")
    removed, changed = datalog.rewrite_jsonl(f, lambda e: {**e, "seen": True}, dry_run=False)
    rows = [json.loads(ln) for ln in f.read_text(encoding="utf-8").split("\n") if ln.strip()]
    assert (removed, changed) == (0, 1)
    assert rows == [{"ts": "2026-10-22T10:00:00", "q": "a b", "uid": "u", "seen": True}]


def test_old_raw_record_is_not_split_by_the_rewrite(tmp_path):
    f = tmp_path / "usage.jsonl"
    f.write_text(json.dumps({"q": "a b", "uid": "u"}, ensure_ascii=False) + "\n", encoding="utf-8")
    datalog.rewrite_jsonl(f, lambda e: e, dry_run=False)
    assert len([ln for ln in f.read_text(encoding="utf-8").split("\n") if ln.strip()]) == 1
