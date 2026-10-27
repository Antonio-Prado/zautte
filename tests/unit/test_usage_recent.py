"""GET /usage/summary counts the users' questions of today, of the last 7 and of the last 30 days without the
test accounts (TEST_ACCOUNTS, by id, name or e-mail): the counter of /health is everything since the start,
tests included, and said little about how much the assistant is used."""
import asyncio
import datetime
import json

from api import main


def test_recent_counts_leave_out_test_accounts(tmp_path, monkeypatch):
    today = main.now_local().date()
    day = lambda n: (today - datetime.timedelta(days=n)).isoformat()
    rows = [("u1", 0), ("u1", 0), ("u2", 3), ("u2", 10), ("u3", 40), ("zz-test", 0), ("t2", 1)]
    (tmp_path / "usage.jsonl").write_text(
        "".join(json.dumps({"ts": f"{day(n)}T10:00:00+01:00", "uid": uid}) + "\n" for uid, n in rows), encoding="utf-8")
    (tmp_path / "users.json").write_text(json.dumps([
        {"id": "u1", "name": "Laura"}, {"id": "u2", "name": "Marco"}, {"id": "u3", "name": "Anna"},
        {"id": "zz-test", "name": "Prova (zz-test)", "email": "zz-test@example.invalid"},
        {"id": "t2", "name": "Seconda prova", "email": "prova2@example.org"}]), encoding="utf-8")
    monkeypatch.setattr(main, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(main, "TEST_ACCOUNTS", frozenset({"zz-test", "prova2@example.org"}))
    d = asyncio.run(main.usage_summary(_=None))
    assert d["recent"] == {"today": 2, "last_7_days": 3, "last_30_days": 4, "users_30_days": 2}
    assert d["test_messages"] == 2
    assert d["total_messages"] == 7
    assert {u["uid"] for u in d["users"] if u.get("test")} == {"zz-test", "t2"}


def test_without_test_accounts_everything_counts(tmp_path, monkeypatch):
    today = main.now_local().date().isoformat()
    (tmp_path / "usage.jsonl").write_text(json.dumps({"ts": f"{today}T09:00:00+01:00", "uid": "zz-test"}) + "\n",
                                          encoding="utf-8")
    monkeypatch.setattr(main, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(main, "TEST_ACCOUNTS", frozenset())
    d = asyncio.run(main.usage_summary(_=None))
    assert d["recent"]["today"] == 1 and d["test_messages"] == 0
