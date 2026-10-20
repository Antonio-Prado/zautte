"""
Advisory lock shared by everything that appends to or rewrites a data file.

The API appends lines to the JSONL files (usage, answers, gaps, feedback) while
`scripts/purge_logs.py` and `scripts/forget.py` rewrite them: without a lock a
line written between the last read and the replacement of the file is lost.
Whoever appends or rewrites takes the lock of the file (`<name>.lock` next to
it) first. The lock file may be created by either account (the API's, root's):
it is world-writable, and an account that cannot open it for writing opens it
read-only, which is enough for flock.
"""

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def file_lock(path: Path):
    lock = path.with_name(path.name + ".lock")
    try:
        fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o666)
        try:
            os.chmod(lock, 0o666)
        except OSError:
            pass   # not ours: already created by the other account
    except PermissionError:
        fd = os.open(lock, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
