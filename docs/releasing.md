# Releasing

Zautte is released in small steps: every finished change to the public code gets its own
version, never a batch.

1. Change the code and add a section to `CHANGELOG.md` ([Keep a Changelog](https://keepachangelog.com/),
   [SemVer](https://semver.org/): patch for fixes, minor for new features; `0.x` while it is a pilot).
2. Set the same version in `version.json`, `CITATION.cff` (also `date-released`) and `publiccode.yml` (also `releaseDate`).
   `python scripts/check_version.py` checks the three files and the changelog; CI runs it.
3. Commit and push, then wait for the green pipeline (lint, version check, accessibility, CodeQL).
4. Deploy and verify online (for example with the hashes of the files).
5. Create the annotated tag `vX.Y.Z` on the commit: `git tag -a vX.Y.Z -m "vX.Y.Z" && git push origin vX.Y.Z`.
   Tags `v*` are protected (they cannot be moved or deleted); the `Release` workflow checks the tag
   against `version.json` and creates the GitHub release with the notes of that changelog section.

Changes that only touch an installation's own files (known facts, texts, logos, see
[Configuration](configuration.md)) are not released: they are deployed and recorded in the
installation's own changelog.
