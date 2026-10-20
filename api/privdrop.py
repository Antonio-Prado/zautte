"""
Maintenance scripts that work in the data folder give up root first.

The data files belong to the service account, which can plant links or swap files
in the folder. A script started as root (purge_logs, adduser) therefore becomes
that account before it reads or writes anything: root never touches what the
service account controls.

The account is named explicitly with SERVICE_USER (in .env or the environment).
Without it, it is the owner of the existing data files. If neither tells who it is,
or the switch does not take effect, the script stops: it never goes on as root.
"""

import os
import pwd
from pathlib import Path

_PROBES = ("users.json", "stats.json", "usage.jsonl", "feedback.jsonl", "answers.jsonl")


def _service_user(data_dir: Path) -> pwd.struct_passwd:
    name = os.environ.get("SERVICE_USER", "").strip()
    if name:
        try:
            return pwd.getpwnam(name)
        except KeyError:
            raise SystemExit(f"SERVICE_USER={name}: no such account") from None
    for probe in _PROBES:
        try:
            st = os.lstat(data_dir / probe)
        except OSError:
            continue
        if st.st_uid != 0:
            return pwd.getpwuid(st.st_uid)
    raise SystemExit("Running as root, and the service account is unknown: set SERVICE_USER "
                     "in .env, or run this as the service account.")


def drop_to_service_user(data_dir: Path) -> None:
    if os.geteuid() != 0:
        return
    pw = _service_user(data_dir)
    if pw.pw_uid == 0:
        raise SystemExit("SERVICE_USER cannot be root.")
    os.initgroups(pw.pw_name, pw.pw_gid)
    os.setgid(pw.pw_gid)
    os.setuid(pw.pw_uid)
    os.environ.update(HOME=pw.pw_dir, USER=pw.pw_name, LOGNAME=pw.pw_name)
    if 0 in (os.getuid(), os.geteuid(), os.getgid(), os.getegid()) or 0 in os.getgroups():
        raise SystemExit("Could not give up root: stopping.")
    try:
        os.setuid(0)
    except PermissionError:
        return
    raise SystemExit("Root could be regained: stopping.")
