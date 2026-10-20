"""
Maintenance scripts that work in the data folder give up root first.

The data folder belongs to the service account, which can plant links or swap
files there. A script started as root (purge_logs, adduser) therefore switches to
the owner of the folder before it reads or writes anything: root never touches
the files the service account controls. Does nothing when not root, or when the
folder is root's own.
"""

import grp
import os
import pwd
from pathlib import Path


def drop_to_data_owner(data_dir: Path) -> None:
    if os.geteuid() != 0:
        return
    st = os.stat(data_dir)
    if st.st_uid == 0:
        return
    user = pwd.getpwuid(st.st_uid).pw_name
    try:
        gid = grp.getgrnam(user).gr_gid
    except KeyError:
        gid = st.st_gid
    os.initgroups(user, gid)
    os.setgid(gid)
    os.setuid(st.st_uid)
    if os.geteuid() == 0 or os.getuid() == 0:
        raise RuntimeError("could not drop root")
