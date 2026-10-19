"""
Controlla che la versione sia la stessa in version.json, nell'ultima voce di CHANGELOG.md
(la prima dopo [Unreleased]) e in CITATION.cff; con un argomento (il tag, es. v0.2.0)
controlla anche che il tag coincida. Esce con 1 se qualcosa non torna.

Uso:  python scripts/check_version.py [vX.Y.Z]
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEMVER = r"\d+\.\d+\.\d+"


def main(argv: list[str]) -> int:
    version = json.loads((ROOT / "version.json").read_text(encoding="utf-8"))["version"]
    errors = []
    if not re.fullmatch(SEMVER, version):
        errors.append(f"version.json: «{version}» non è una versione X.Y.Z")
    entry = re.search(rf"^## \[({SEMVER})\] - (\d{{4}}-\d{{2}}-\d{{2}})$",
                      (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), re.MULTILINE)
    if not entry:
        errors.append("CHANGELOG.md: nessuna voce «## [X.Y.Z] - AAAA-MM-GG»")
    elif entry.group(1) != version:
        errors.append(f"CHANGELOG.md: l'ultima voce è {entry.group(1)}, version.json dice {version}")
    cff = re.search(rf"^version: ({SEMVER})$", (ROOT / "CITATION.cff").read_text(encoding="utf-8"), re.MULTILINE)
    if not cff or cff.group(1) != version:
        errors.append(f"CITATION.cff: versione {cff.group(1) if cff else 'assente'}, version.json dice {version}")
    if argv and argv[0] != f"v{version}":
        errors.append(f"il tag {argv[0]} non coincide con version.json (v{version})")
    for e in errors:
        print("KO  " + e)
    if not errors:
        print(f"ok  versione {version} coerente (version.json, CHANGELOG.md, CITATION.cff)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
