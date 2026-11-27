"""The weekly sync must not lose the race for the lock with the news sync (0.56.7)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _lockf_wait(script: str) -> str:
    text = (ROOT / "scripts" / script).read_text(encoding="utf-8")
    return re.search(r"^lockf -t (\S+) ", text, re.MULTILINE)[1]


def test_the_weekly_sync_waits_for_the_lock():
    text = (ROOT / "scripts" / "incremental_sync.sh").read_text(encoding="utf-8")
    assert _lockf_wait("incremental_sync.sh") == '"$WAIT_SECONDS"'
    default = re.search(r'^WAIT_SECONDS="\$\{SYNC_LOCK_WAIT:-(\d+)\}"$', text, re.MULTILINE)
    assert default and int(default[1]) >= 1800   # longer than a news run may last (SYNC_TIMEOUT, 25 minutes)


def test_the_frequent_jobs_still_skip_when_the_lock_is_held():
    for script in ("news_sync.sh", "acts_sync.sh", "amt_sync.sh"):
        assert _lockf_wait(script) == "0", script


def test_the_documented_schedule_does_not_start_it_with_the_news_sync():
    docs = (ROOT / "docs" / "operations.md").read_text(encoding="utf-8")
    line = re.search(r"^(\d+)\s+(\d+)\s+\S+\s+\S+\s+\S+\s+zsync \S*incremental_sync\.sh", docs, re.MULTILINE)
    assert line, "the /etc/crontab line of incremental_sync.sh is in docs/operations.md"
    assert int(line[1]) % 15 != 0   # news_sync.sh starts every 15 minutes and takes the lock first
    header = (ROOT / "scripts" / "incremental_sync.sh").read_text(encoding="utf-8")
    assert f"#   {line[1]} {line[2]} * * 1 zsync" in header
