"""
Writing data files safely when the folder is writable by a less privileged account.

The API runs as an unprivileged user that owns the data folder, while maintenance
scripts (purge, forget, adduser) may run as root in the same folder. A fixed
temporary name or a path opened by name would let the unprivileged account plant a
symbolic link there and have root truncate, chmod or chown some other file.
Here a temporary file is created exclusively with a random name, permissions and
ownership are applied through the open descriptor, and a target that is not a
regular file (a link, for instance) is refused.
"""

import contextlib
import os
import stat
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import IO


def require_regular(path: Path) -> os.stat_result | None:
    """The lstat of `path`, None if it does not exist; OSError if it is not a regular file."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(st.st_mode):
        raise OSError(f"{path.name} is not a regular file")
    return st


@contextlib.contextmanager
def atomic_file(path: Path, mode: int = 0o600) -> Iterator[IO[str]]:
    """Text file replacing `path` when the block ends without error.

    An existing file keeps its permissions and, when running as root, its owner;
    a new one gets `mode` and, as root, the owner of the folder."""
    path = Path(path)
    old = require_regular(path)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    tmp = Path(name)
    try:
        if old is not None:
            os.fchmod(fd, stat.S_IMODE(old.st_mode))
            ref = old
        else:
            os.fchmod(fd, mode)
            ref = os.stat(path.parent)
        if os.geteuid() == 0:
            os.fchown(fd, ref.st_uid, ref.st_gid)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yield f
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def write_text_atomic(path: Path, text: str, mode: int = 0o600) -> None:
    with atomic_file(path, mode) as f:
        f.write(text)


def ensure_private(paths: list[Path]) -> list[str]:
    """Makes the data files (and their lock files) readable by their owner only.

    A missing file is created exclusively, mode 600, so that another account that can write in
    the folder cannot create it first. An existing regular file of this process loses any group
    or world access. What cannot be fixed (a link, a file of another account) is returned as a
    problem to log. Never follows links."""
    problems: list[str] = []
    for path in paths:
        for p in (Path(path), Path(path).with_name(Path(path).name + ".lock")):
            try:
                st = os.lstat(p)
            except FileNotFoundError:
                try:
                    os.close(os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600))
                except OSError as e:
                    problems.append(f"{p.name}: cannot create ({e.strerror})")
                continue
            except OSError as e:
                problems.append(f"{p.name}: {e.strerror}")
                continue
            if not stat.S_ISREG(st.st_mode):
                problems.append(f"{p.name}: not a regular file")
            elif st.st_uid != os.geteuid():
                problems.append(f"{p.name}: owned by uid {st.st_uid}, not by this process")
            elif st.st_mode & 0o077:
                try:
                    os.chmod(p, 0o600)
                except OSError as e:
                    problems.append(f"{p.name}: cannot restrict permissions ({e.strerror})")
    return problems
