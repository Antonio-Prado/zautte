"""The pages the known facts come from are read again: what counts as a change, what is reported
and until when."""
import json
import re
from datetime import date

from api.knownfacts import watch_targets
from scripts import known_facts_report as report
from scripts import known_facts_watch as watch

TAX = {"keywords": ["imu"], "title": "Property tax 2026", "source": "https://example.org/tax", "text": "..."}
XMAS = {"keywords": ["natale"], "title": "Christmas programme: not published yet",
        "source": "https://example.org/events", "text": "...",
        "watch": {"url": "https://example.org/calendar", "appears": "christmas (holidays|village) 2026"}}
HOME = {"keywords": ["x"], "title": "Home", "source": "https://example.org/", "text": "...", "watch": False}
NO_PAGE = {"keywords": ["y"], "title": "No page", "source": "", "text": "..."}
PAGE = b"""<html><head><title>T</title><script>var a = 1;</script></head><body>
<header>Menu<nav>Home | News</nav></header>
<main><h1>Property tax</h1><p>Balance by <b>16 December</b>.</p><p>Numero accessi: 207 (2026)</p></main>
<footer>Town hall, 1 Main Street</footer></body></html>"""
IGNORE = [re.compile("^Numero accessi", re.IGNORECASE)]


def test_which_pages_are_watched_and_the_key_follows_the_facts():
    targets = watch_targets([TAX, XMAS, HOME, NO_PAGE, "not a fact"])
    assert sorted(targets) == ["https://example.org/calendar", "https://example.org/tax"]
    assert targets["https://example.org/calendar"]["appears"] == ["christmas (holidays|village) 2026"]
    assert targets["https://example.org/tax"]["titles"] == ["Property tax 2026"]
    edited = watch_targets([{**TAX, "review_by": "2027-01-10"}, XMAS])
    assert edited["https://example.org/tax"]["key"] != targets["https://example.org/tax"]["key"]
    assert edited["https://example.org/calendar"]["key"] == targets["https://example.org/calendar"]["key"]
    # two facts from the same page: one address, both titles
    both = watch_targets([TAX, {**TAX, "title": "Rates"}])
    assert both["https://example.org/tax"]["titles"] == ["Property tax 2026", "Rates"]


def test_only_the_main_text_is_compared_without_the_lines_that_change_by_themselves():
    text = watch.page_text(PAGE, "text/html; charset=utf-8", IGNORE)
    assert text == "Property tax\nBalance by\n16 December\n."
    assert watch.page_text(PAGE.replace(b"207", b"208"), "text/html", IGNORE) == text
    assert watch.page_text(PAGE.replace(b"Menu", b"Other menu"), "text/html", IGNORE) == text
    assert watch.page_text(PAGE.replace(b"16 December", b"18 December"), "text/html", IGNORE) != text
    pdf = watch.page_text(b"%PDF-1.7 ...", "application/pdf", IGNORE)
    assert pdf.startswith("sha256:") and pdf != watch.page_text(b"%PDF-1.7 ....", "application/pdf", IGNORE)


def _target(entry=TAX):
    return next(iter(watch_targets([entry]).values()))


def _kinds(record):
    return [e["kind"] for e in record["log"]]


def test_the_first_reading_reports_nothing_and_a_change_opens_one_alert_with_its_lines():
    first = watch.check(None, 200, "Balance by\n16 December", "2026-11-28", _target())
    assert first["opened"] is None and first["log"] == [] and first["status"] == 200 and first["fails"] == 0
    same = watch.check(first, 200, "Balance by\n16 December", "2026-11-29", _target())
    assert same["opened"] is None and same["ref_text"] is None
    changed = watch.check(same, 200, "Balance by\n18 December\nNew rates", "2026-11-30", _target())
    assert (changed["opened"], changed["changed"], changed["ref_key"]) == ("2026-11-30", "2026-11-30", _target()["key"])
    assert changed["added"] == ["18 December", "New rates"] and changed["removed"] == ["16 December"]
    assert changed["text"] == "Balance by\n18 December\nNew rates" and changed["ref_text"] == "Balance by\n16 December"
    # it changes again: still one alert, open since the first day, with everything that changed since
    again = watch.check(changed, 200, "Balance by\n20 December\nNew rates", "2026-12-03", _target())
    assert (again["opened"], again["changed"]) == ("2026-11-30", "2026-12-03")
    assert again["added"] == ["20 December", "New rates"] and again["removed"] == ["16 December"]
    # the page goes back to what it said: nothing is open any more
    back = watch.check(again, 200, "Balance by\n16 December", "2026-12-04", _target())
    assert back["opened"] is None and back["added"] == [] and back["ref_text"] is None


def test_editing_the_fact_closes_the_alert_and_the_page_is_compared_with_what_it_said_then():
    target = _target()
    record = watch.check(None, 200, "16 December", "2026-11-28", target)
    record = watch.check(record, 200, "18 December", "2026-11-30", target)
    assert record["opened"] == "2026-11-30"
    edited = _target({**TAX, "review_by": "2027-01-10"})                 # the keeper looked and moved the date
    after = watch.check(record, 200, "18 December", "2026-12-01", edited)
    assert after["opened"] is None and after["ref_key"] == edited["key"] and after["added"] == []
    later = watch.check(after, 200, "20 December", "2026-12-05", edited)
    assert later["opened"] == "2026-12-05" and later["removed"] == ["18 December"]
    # edited, and the page changed again before the next reading: compared with the reading before the edit
    both = watch.check(record, 200, "20 December", "2026-12-01", edited)
    assert both["opened"] == "2026-12-01" and both["removed"] == ["18 December"] and both["added"] == ["20 December"]


def test_a_page_gone_is_reported_once_keeps_its_text_and_may_come_back():
    first = watch.check(None, 200, "text", "2026-11-28", _target())
    gone = watch.check(first, 410, None, "2026-11-29", _target())
    assert gone["gone_since"] == "2026-11-29" and _kinds(gone) == ["gone"] and gone["text"] == "text"
    still = watch.check(gone, 404, None, "2026-11-30", _target())
    assert still["gone_since"] == "2026-11-29" and _kinds(still) == ["gone"]
    back = watch.check(still, 200, "text", "2026-12-01", _target())
    assert back["gone_since"] is None and _kinds(back) == ["gone", "back"] and back["opened"] is None
    never_there = watch.check(None, 404, None, "2026-11-28", _target())      # gone at the first reading: told
    assert never_there["gone_since"] == "2026-11-28"


def test_a_page_that_cannot_be_read_is_reported_after_three_runs_and_keeps_what_it_had():
    record = watch.check(None, 200, "text", "2026-11-28", _target())
    for n, day in enumerate(("2026-11-29", "2026-11-30"), 1):
        record = watch.check(record, None, None, day, _target(), "ConnectTimeout: timed out")
        assert record["fails"] == n and record["log"] == [] and record["text"] == "text"
    record = watch.check(record, 503, None, "2026-12-01", _target())
    assert record["fails"] == 3 and _kinds(record) == ["unreachable"]
    record = watch.check(record, 503, None, "2026-12-02", _target())
    assert record["fails"] == 4 and _kinds(record) == ["unreachable"]       # noted once
    assert watch.check(record, 200, "text", "2026-12-03", _target())["fails"] == 0


def test_what_a_fact_waits_for_stays_open_however_often_the_page_changes():
    target = _target(XMAS)
    cid = "appears:christmas (holidays|village) 2026"
    record = watch.check(None, 200, "Autumn events", "2026-11-26", target)
    assert record["conds"] == {}
    record = watch.check(record, 200, "Autumn events\nSBT Christmas Holidays 2026", "2026-11-27", target)
    assert record["conds"] == {cid: {"since": "2026-11-27", "first": False, "key": target["key"]}}
    for day in range(1, 20):                                              # a calendar changes every day
        record = watch.check(record, 200, f"Events of day {day}\nSBT Christmas Holidays 2026", f"2026-12-{day:02d}", target)
    assert record["conds"][cid]["since"] == "2026-11-27" and len(record["log"]) == watch.MAX_LOG
    assert record["opened"] == "2026-11-27" and record["changed"] == "2026-12-19"
    # the fact is rewritten but still asks for the same thing: the condition holds, and is known
    rewritten = _target({**XMAS, "title": "Christmas programme"})
    record = watch.check(record, 200, "SBT Christmas Holidays 2026", "2026-12-20", rewritten)
    assert record["conds"][cid]["key"] == target["key"] != rewritten["key"]
    # it was already there the first time the page was read: said so
    already = watch.check(None, 200, "Christmas Village 2026 opens", "2026-11-28", target)
    assert already["conds"][cid]["first"] is True
    # the opposite: a fact built on something the page says today
    gone_away = _target({**TAX, "watch": {"disappears": "agenda is empty"}})
    was = watch.check(None, 200, "The agenda is empty", "2026-11-28", gone_away)
    assert was["conds"] == {}
    now = watch.check(was, 200, "Book a slot", "2026-11-29", gone_away)
    assert list(now["conds"]) == ["disappears:agenda is empty"]
    assert watch.check(now, 200, "The agenda is empty", "2026-11-30", gone_away)["conds"] == {}


def test_a_run_reads_every_page_once_and_drops_the_pages_no_fact_names_any_more():
    pages = {"https://example.org/tax": (200, "tax text", ""), "https://example.org/calendar": (None, None, "boom")}
    asked = []

    def read(url):
        asked.append(url)
        return pages[url]

    state = {"https://example.org/old": {"hash": "x", "text": "old"}, "https://example.org/tax": "broken"}
    new = watch.watch(watch_targets([TAX, XMAS, HOME]), state, read, "2026-11-28")
    assert sorted(asked) == sorted(pages) and sorted(new) == sorted(pages)
    assert new["https://example.org/tax"]["text"] == "tax text" and new["https://example.org/calendar"]["fails"] == 1


def test_the_reader_waits_between_two_requests_to_the_same_site(monkeypatch):
    class Response:
        status_code, headers, content = 200, {"content-type": "text/html"}, b"<main>ok</main>"

    calls, sleeps, now = [], [], [100.0]
    monkeypatch.setattr(watch, "get_limited", lambda client, url, domains: calls.append(url) or Response())
    read = watch.reader(None, ["example.org"], [], delay=5, sleep=lambda s: sleeps.append(s) or now.__setitem__(0, now[0] + s),
                        clock=lambda: now[0])
    assert read("https://example.org/a") == (200, "ok", "") and sleeps == []
    assert read("https://other.example/b")[0] == 200 and sleeps == []            # another site: no wait
    assert read("https://example.org/c")[0] == 200 and sleeps == [5]

    def broken(client, url, domains):
        raise OSError("no route")

    monkeypatch.setattr(watch, "get_limited", broken)
    status, text, error = read("https://third.example/")
    assert status is None and text is None and error.startswith("OSError")


def test_the_state_file_round_trip(tmp_path):
    path = tmp_path / "facts" / "known_facts_watch.json"
    assert watch.read_state(path) == {}
    watch.write_state(path, {"https://example.org/tax": {"hash": "h", "text": "t"}})
    assert watch.read_state(path)["https://example.org/tax"]["text"] == "t"
    assert json.loads(path.read_text(encoding="utf-8"))


# ── the report ───────────────────────────────────────────────────────────────────────────────────────
def _state(changed_on="2026-11-30"):
    tax, calendar = watch_targets([TAX, XMAS]).values()
    record = watch.check(None, 200, "Balance by\n16 December", "2026-11-28", tax)
    record = watch.check(record, 200, "Balance by\n18 December", changed_on, tax)
    gone = watch.check(watch.check(None, 404, None, "2026-11-28", calendar), 404, None, changed_on, calendar)
    return {"https://example.org/tax": record, "https://example.org/calendar": gone}


def _watch_kinds(entries, state, today):
    return [(i["kind"], i["new"]) for i in report.sort_items(report.watch_items(entries, state, today))]


def test_the_report_lists_the_pages_gone_and_changed_until_the_fact_is_edited():
    today = date(2026, 11, 30)
    items = report.sort_items(report.watch_items([TAX, XMAS], _state(), today))
    assert [(i["kind"], i["new"]) for i in items] == [("page_gone", False), ("page_changed", True)]
    assert report.should_send(items, today, 7)                         # a page changed this morning
    # the job may run after the report: what it found yesterday is still new the morning after
    assert _watch_kinds([TAX, XMAS], _state(), date(2026, 12, 1)) == [("page_gone", False), ("page_changed", True)]
    state = _state()
    for record in state.values():
        record["checked"] = "2026-12-07"                               # the job goes on reading every day
    wednesday, monday = date(2026, 12, 2), date(2026, 12, 7)
    assert not report.should_send(report.watch_items([TAX, XMAS], state, wednesday), wednesday, 7)   # already told
    assert report.should_send(report.watch_items([TAX, XMAS], state, monday), monday, 7)
    # the fact is edited (its check date is moved): the change is closed at once; the page gone stays
    assert _watch_kinds([{**TAX, "review_by": "2027-01-10"}, XMAS], state, monday) == [("page_gone", False)]
    # the facts name other pages, or none: nothing left
    assert report.watch_items([{**TAX, "source": "https://example.org/new"}, {**XMAS, "watch": False}], state, monday) == []
    subject, body = report.build_report(items, today, "Comune di Esempio", 7)
    assert subject == "Zautte: 2 fatti noti da rivedere (1 già scaduto o in ritardo)"
    assert "== Pagine di origine che non esistono più: 1\n- https://example.org/calendar\n  non esiste più (dal 28/11/2026)" in body
    assert "- https://example.org/tax\n  cambiata il 30/11/2026\n  + 18 December\n  − 16 December\n  fatto: «Property tax 2026»" in body


def test_a_condition_and_a_page_not_read_are_listed_and_the_job_that_stopped_too():
    target = watch_targets([XMAS])["https://example.org/calendar"]
    record = watch.check(None, 200, "Autumn", "2026-11-26", target)
    record = watch.check(record, 200, "SBT Christmas Holidays 2026", "2026-11-27", target)
    state = {"https://example.org/calendar": record}
    items = report.sort_items(report.watch_items([XMAS], state, date(2026, 11, 27)))
    assert [(i["kind"], i["new"]) for i in items] == [("page_appears", True), ("page_changed", True)]
    _, body = report.build_report(items, date(2026, 11, 27), "X", 7)
    assert "dal 27/11/2026 contiene «christmas (holidays|village) 2026»" in body
    assert "fatto: «Christmas programme: not published yet»" in body
    # the fact no longer asks for it: the condition is not listed
    assert [i["kind"] for i in report.watch_items([{**XMAS, "watch": {"url": "https://example.org/calendar"}}],
                                                  state, date(2026, 11, 27))] == []
    for day in ("2026-11-28", "2026-11-29", "2026-11-30"):
        record = watch.check(record, None, None, day, target, "ReadTimeout")
    unread = report.watch_items([XMAS], {"https://example.org/calendar": record}, date(2026, 11, 30))
    assert ("page_unread", True) in [(i["kind"], i["new"]) for i in unread]
    # nobody has read the pages for two days: said the second day, then on Mondays
    stopped = report.watch_items([XMAS], {"https://example.org/calendar": record}, date(2026, 12, 2))
    assert ("watch_stopped", True) in [(i["kind"], i["new"]) for i in stopped]
    _, body = report.build_report(report.sort_items(stopped), date(2026, 12, 2), "X", 7)
    assert "== Il controllo delle pagine di origine non sta girando: 1\n- ultima lettura delle pagine: 30/11/2026 (2 giorni fa)" in body
    later = report.watch_items([XMAS], {"https://example.org/calendar": record}, date(2026, 12, 4))
    assert ("watch_stopped", False) in [(i["kind"], i["new"]) for i in later]


def test_what_a_page_says_goes_into_the_mail_as_plain_short_lines():
    state = _state()
    state["https://example.org/tax"]["added"] = ["x" * 900, "a\x00b\x1b[31m\nc"] + ["l"] * 20
    (item,) = [i for i in report.watch_items([TAX], state, date(2026, 11, 30)) if i["kind"] == "page_changed"]
    assert len(item["added"]) == report.MAX_PAGE_LINES and len(item["added"][0]) == report.MAX_PAGE_LINE
    assert item["added"][1] == "a b [31m c"
    # a state that is not what the job writes is read without errors
    today = date(2026, 11, 30)
    assert report.watch_items([TAX], {"https://example.org/tax": "x"}, today) == []
    odd = {"https://example.org/tax": {"checked": "2026-11-30", "fails": "3", "added": "x", "conds": [], "opened": 1,
                                       "ref_key": None}}
    assert [i["kind"] for i in report.watch_items([TAX], odd, today)] == ["page_unread"]
    assert report.watch_items([TAX], {"https://example.org/tax": {"fails": "many"}}, today) == []


def test_a_dynamic_fact_no_job_writes_any_more_is_listed():
    dynamic = [{"id": "amt-pay", "title": "Pay of the administrators", "updated": "2026-11-23", "source": "https://p/173"},
               {"id": "events-upcoming", "title": "Events", "updated": "2026-11-28"},
               {"id": "no-day", "title": "No day"}, {"id": "bad", "title": "Bad", "updated": "yesterday"}]
    today = date(2026, 11, 28)
    (item,) = report.stale_items(dynamic, today, 3)
    assert item["kind"] == "stale" and item["age"] == 5 and item["new"] is False
    assert report.stale_items(dynamic, date(2026, 11, 27), 3)[0]["new"] is True       # the first day over the limit
    assert report.stale_items(dynamic, today, 0) == []                                # 0 = not checked
    assert not report.should_send([item], today, 7) and report.should_send([item], date(2026, 11, 30), 7)
    _, body = report.build_report([item], today, "X", 7)
    assert "== Schede automatiche ferme: 1\n- «Pay of the administrators»\n  scritta l'ultima volta il 23/11/2026 (5 giorni fa); job: amt-pay" in body
