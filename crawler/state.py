"""
Crawler state management: tracks what has been indexed, when, and with which
content hash. Enables incremental crawling.

State saved in: data/crawl_cache/crawl_state.json
"""

import hashlib
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import CRAWL_CACHE_DIR, DOCUMENTS_DIR

log = logging.getLogger(__name__)

STATE_FILE = CRAWL_CACHE_DIR / "crawl_state.json"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def content_hash(data: str | bytes) -> str:
    """MD5 hash of the content (to detect changes)."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.md5(data).hexdigest()


def write_json_atomic(path: Path, data, indent: int = 2) -> None:
    """Writes `data` as JSON next to `path` and renames it over: a job stopped halfway (timeout,
    crash) never leaves a truncated file behind. An existing file keeps its permissions; a new
    one gets those of an ordinary write (the umask decides: the API reads some of these files)."""
    import contextlib
    import os
    path = Path(path)
    try:
        mode = os.stat(path).st_mode & 0o777
    except FileNotFoundError:
        mode = None
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "x", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        if mode is not None:
            os.chmod(tmp, mode)   # the permissions the file already had, whatever they are
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):   # the temporary file may not exist yet
            os.unlink(tmp)
        raise


class CrawlState:
    """
    Persistent dictionary: URL → {hash, last_crawled, title, file, changed}.
    """

    def __init__(self):
        CRAWL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._state: dict = {}
        self._orphans: set[str] = set()   # files a URL no longer uses (it was saved under a new name)
        self._load()

    def _load(self):
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE, encoding="utf-8") as f:
                    self._state = json.load(f)
                log.info(f"State loaded: {len(self._state)} tracked URLs")
            except (OSError, ValueError) as e:
                log.warning(f"Unable to load state: {e} — starting from scratch")
                self._state = {}

    def save(self):
        write_json_atomic(STATE_FILE, self._state, indent=2)

    def is_changed(self, url: str, new_hash: str) -> bool:
        """Return True if the content is new or changed since the last scan."""
        entry = self._state.get(url)
        if entry is None:
            return True  # never seen before
        return entry.get("hash") != new_hash

    def update(self, url: str, new_hash: str, title: str = "", file: str = ""):
        """Update the state for a URL."""
        was_new = url not in self._state
        previous = (self._state.get(url) or {}).get("file")
        if previous and file and previous != file:
            self._orphans.add(previous)
        self._state[url] = {
            "hash": new_hash,
            "last_crawled": _now_iso(),
            "title": title,
            "file": file,
            "changed": True,
        }
        return was_new

    def delete_orphans(self) -> int:
        """Delete the files that URLs stopped using. Called once the state and the index that name
        the new files are saved: an interrupted crawl never leaves an index pointing at a deleted
        file. Only files inside the cache folders, and not used by any other URL, are removed."""
        in_use = {e.get("file") for e in self._state.values() if e.get("file")}
        bases = [CRAWL_CACHE_DIR.resolve(), DOCUMENTS_DIR.resolve()]
        removed = 0
        for name in self._orphans - in_use:
            path = Path(name)
            try:
                if path.is_file() and any(path.resolve().is_relative_to(b) for b in bases):
                    path.unlink()
                    removed += 1
            except OSError as e:
                log.warning(f"Old cache file not removed ({path.name}): {e}")
        self._orphans.clear()
        return removed

    def mark_unchanged(self, url: str):
        """Mark a URL as unchanged in this session."""
        if url in self._state:
            self._state[url]["changed"] = False

    def reset_changed_flags(self):
        """Clear all 'changed' flags before a new session."""
        for entry in self._state.values():
            entry["changed"] = False

    def get_changed_urls(self) -> list[str]:
        """Return the URLs changed in the last session."""
        return [url for url, e in self._state.items() if e.get("changed")]

    def get_removed_urls(self, seen_urls: set[str]) -> list[str]:
        """Return the known URLs that were not found during the crawl."""
        return [url for url in self._state if url not in seen_urls]

    def remove(self, url: str):
        """Remove a URL from the state (page deleted from the site)."""
        self._state.pop(url, None)

    def stats(self) -> dict:
        total = len(self._state)
        changed = len(self.get_changed_urls())
        return {"total_tracked": total, "changed_last_run": changed}

    def __contains__(self, url: str) -> bool:
        return url in self._state
