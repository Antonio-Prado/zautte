"""
Adds the SHA-256 of every published file to requirements-lock.txt.

With a hash on each line pip checks what it downloads against the list and refuses anything
else (a changed or substituted package), on any platform: the list holds the wheels and the
source archive of each pinned version.

Usage (needs the network; run after changing a version in requirements-lock.txt):
    python scripts/update_lock_hashes.py
"""

import json
import re
import sys
import urllib.request
from pathlib import Path

LOCK = Path(__file__).resolve().parent.parent / "requirements-lock.txt"
PIN = re.compile(r"^([A-Za-z0-9_.\-]+)==([^\s;\\]+)")


def hashes(name: str, version: str) -> list[str]:
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    with urllib.request.urlopen(url, timeout=30) as resp:
        files = json.load(resp)["urls"]
    return sorted({f["digests"]["sha256"] for f in files})


def main() -> int:
    out: list[str] = []
    for line in LOCK.read_text(encoding="utf-8").splitlines():
        m = PIN.match(line)
        if not m:
            if not line.startswith("    --hash="):
                out.append(line)
            continue
        found = hashes(m.group(1), m.group(2))
        if not found:
            print(f"No files on PyPI for {m.group(1)}=={m.group(2)}", file=sys.stderr)
            return 1
        pin = f"{m.group(1)}=={m.group(2)}"
        out.append(pin + " \\")
        out.extend(f"    --hash=sha256:{h}" + (" \\" if i < len(found) - 1 else "") for i, h in enumerate(found))
    LOCK.write_text("\n".join(out) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
