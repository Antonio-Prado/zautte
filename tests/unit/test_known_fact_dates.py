"""A known fact with an end date is left out from the day after it; the report lists the dates
that are near, past or mistyped, and is sent only on the days something happens."""
from datetime import date, datetime

import pytest

from api import rag
from api.knownfacts import fact_date, is_expired
from scripts import known_facts_report as report

FAIR = {"keywords": ["fiera"], "title": "Fair of 12-13 December", "source": "https://example.org/fair",
        "text": "...", "expires": "2026-12-13"}
TAX = {"keywords": ["tari"], "title": "How to pay the waste tax", "source": "https://example.org/tax",
       "text": "...", "review_by": "2026-12-02"}
PLAIN = {"keywords": ["orari"], "title": "Opening hours", "source": "", "text": "..."}


def _titles(query, entries, today, monkeypatch):
    monkeypatch.setattr(rag, "_KNOWN_FACTS", rag._parse_known_facts(entries))
    monkeypatch.setattr(rag, "_dynamic_known_facts", list)
    monkeypatch.setattr(rag, "now_local", lambda: datetime.combine(today, datetime.min.time()))
    return [c["title"] for c in rag._inject_known_facts(query)]


def test_a_fact_is_used_up_to_its_last_day_and_left_out_from_the_day_after(monkeypatch):
    assert _titles("quando è la fiera?", [FAIR], date(2026, 12, 13), monkeypatch) == ["Fair of 12-13 December"]
    assert _titles("quando è la fiera?", [FAIR], date(2026, 12, 14), monkeypatch) == []


def test_a_review_date_never_takes_a_fact_out(monkeypatch):
    assert _titles("come pago la tari?", [TAX], date(2027, 6, 1), monkeypatch) == ["How to pay the waste tax"]


def test_a_mistyped_end_date_leaves_the_fact_in_use(monkeypatch):
    wrong = {**FAIR, "expires": "13/12/2026"}
    assert _titles("quando è la fiera?", [wrong], date(2027, 1, 1), monkeypatch) == ["Fair of 12-13 December"]


def test_facts_without_dates_are_used_as_before(monkeypatch):
    assert _titles("orari", [PLAIN], date(2030, 1, 1), monkeypatch) == ["Opening hours"]


def test_the_date_fields():
    assert fact_date(FAIR, "expires") == date(2026, 12, 13)
    assert fact_date(FAIR, "review_by") is None and fact_date({"expires": ""}, "expires") is None
    for wrong in ("13/12/2026", "2026-13-01", 20261213, True):
        with pytest.raises(ValueError):
            fact_date({"expires": wrong}, "expires")
    assert not is_expired(None, date(2030, 1, 1))
    assert not is_expired(date(2026, 12, 13), date(2026, 12, 13))
    assert is_expired(date(2026, 12, 13), date(2026, 12, 14))


def _kinds(entries, today, days=7):
    return [(i["kind"], i["left"]) for i in report.open_items(entries, today, days)]


def test_the_list_holds_what_is_near_past_or_mistyped():
    entries = [FAIR, TAX, PLAIN]
    assert _kinds(entries, date(2026, 11, 20)) == []                       # both dates are far
    assert _kinds(entries, date(2026, 11, 25)) == [("review_soon", 7)]
    assert _kinds(entries, date(2026, 12, 6)) == [("expiring", 7), ("review_overdue", -4)]
    assert _kinds(entries, date(2026, 12, 13)) == [("expiring", 0), ("review_overdue", -11)]
    assert _kinds(entries, date(2026, 12, 20)) == [("expired", -7), ("review_overdue", -18)]
    wrong = {**FAIR, "expires": "13 dicembre"}
    assert _kinds([wrong], date(2026, 1, 1)) == [("bad_date", 0)]


def test_a_mail_goes_out_only_on_the_days_something_happens():
    def sent(entries, today):
        return report.should_send(report.open_items(entries, today, 7), today, 7)

    # the fair: it enters the list a week before, tomorrow is its last day, it is out since today
    assert sent([FAIR], date(2026, 12, 6)) and sent([FAIR], date(2026, 12, 12)) and sent([FAIR], date(2026, 12, 14))
    assert not sent([FAIR], date(2026, 12, 8)) and not sent([FAIR], date(2026, 12, 13))
    assert not sent([FAIR], date(2026, 12, 16))                      # a Wednesday: already told
    assert sent([FAIR], date(2026, 12, 21)) and sent([FAIR], date(2026, 12, 28))   # every Monday until it is edited
    # the check: a week before and on its day, then on Mondays
    assert sent([TAX], date(2026, 11, 25)) and sent([TAX], date(2026, 12, 2))
    assert not sent([TAX], date(2026, 12, 3)) and sent([TAX], date(2026, 12, 7))
    # a mistyped date is told every day; a fact without dates never
    assert sent([{**FAIR, "expires": "13/12/2026"}], date(2026, 1, 7))
    assert not sent([PLAIN], date(2026, 12, 21))


def test_the_mail_says_which_fact_when_and_what_to_do():
    today = date(2026, 12, 14)
    long_title = {**FAIR, "title": "Fiera " + "x" * 300}
    items = report.open_items([FAIR, TAX, {**PLAIN, "review_by": "domani"}, long_title], today, 7)
    subject, body = report.build_report(items, today, "Comune di Esempio", 7)
    assert subject == "Zautte: 4 fatti noti da rivedere (4 già scaduti o in ritardo)"
    assert "== Scaduti: Zautte non li usa più: 2" in body
    assert "- «Fair of 12-13 December»\n  scaduto il 13/12/2026 (ieri)\n  fonte: https://example.org/fair" in body
    assert "era da ricontrollare entro il 02/12/2026 (da 12 giorni)" in body
    assert '"review_by": "domani" non è una data' in body
    assert max(len(line) for line in body.splitlines() if line.startswith("- «")) <= report.MAX_TITLE + 4
    one = report.build_report(report.open_items([FAIR], date(2026, 12, 12), 7), date(2026, 12, 12), "X", 7)
    assert one[0] == "Zautte: 1 fatto noto da rivedere" and "ultimo giorno: 13/12/2026 (domani)" in one[1]
    assert "== In scadenza (entro 7 giorni): 1" in one[1]


def test_the_file_is_read_as_a_list_of_facts(tmp_path):
    f = tmp_path / "known_facts.json"
    f.write_text('[{"keywords": ["a"], "title": "A", "text": "..."}, "not a fact"]', encoding="utf-8")
    assert [e["title"] for e in report.read_entries(f)] == ["A"]
    f.write_text('{"keywords": []}', encoding="utf-8")
    with pytest.raises(TypeError):
        report.read_entries(f)
