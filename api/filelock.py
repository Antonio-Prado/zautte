"""
Advisory lock shared by everything that appends to or rewrites a data file.

The API appends lines to the JSONL files (usage, answers, gaps, feedback) while
`scripts/purge_logs.py` and `scripts/forget.py` rewrite them: without a lock a
line written between the last read and the replacement of the file is lost.
Whoever appends or rewrites takes the lock of the file (`<name>.lock` next to
it) first. The lock file belongs to the owner of the data (root, when it creates
it, hands it over), mode 600. It is never opened through a symbolic link, and only
a file this process has just created is chowned (through its descriptor). If the
lock cannot be taken an OSError is raised: a rewrite must not go on unprotected.
"""

import fcntl
import os
import stat
from contextlib import contextmanager
from pathlib import Path


def _open_lock(path: Path) -> int:
    lock = path.with_name(path.name + ".lock")
    flags = os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_RDWR | flags, 0o600)
    except FileExistsError:
        fd = os.open(lock, os.O_RDONLY | flags)   # a link is refused (ELOOP); flock needs no write access
    else:
        if os.geteuid() == 0:
            # created by root (purge_logs, forget): same owner as the data, so the API can open it
            st = os.stat(path) if path.exists() else os.stat(path.parent)
            os.fchown(fd, st.st_uid, st.st_gid)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(f"{lock.name} is not a regular file")
    except BaseException:
        os.close(fd)
        raise
    return fd


@contextmanager
def file_lock(path: Path):
    fd = _open_lock(path)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)   # closing releases the lock
