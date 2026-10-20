"""
Advisory lock shared by everything that appends to or rewrites a data file.

The API appends lines to the JSONL files (usage, answers, gaps, feedback) while
`scripts/purge_logs.py` and `scripts/forget.py` rewrite them: without a lock a
line written between the last read and the replacement of the file is lost.
Whoever appends or rewrites takes the lock of the file (`<name>.lock` next to
it) first. The lock file belongs to the owner of the data (root, when it creates
it, hands it over), mode 600. If it cannot be opened the work goes on without
the lock, with a warning, rather than losing the line to write.
"""

import fcntl
import logging
import os
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger(__name__)


def _open_lock(path: Path) -> int:
    lock = path.with_name(path.name + ".lock")
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    if os.geteuid() == 0:
        # created by root (purge_logs, forget): same owner as the data, so the API can open it
        ref = path if path.exists() else path.parent
        st = ref.stat()
        os.fchown(fd, st.st_uid, st.st_gid)
    return fd


@contextmanager
def file_lock(path: Path):
    try:
        fd = _open_lock(path)
    except OSError as e:
        log.warning("Lock of %s not available (%s): going on without it", path.name, e)
        yield
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
