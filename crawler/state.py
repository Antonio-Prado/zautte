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
from config.settings import CRAWL_CACHE_DIR

log = logging.getLogger(__name__)

STATE_FILE = CRAWL_CACHE_DIR / "crawl_state.json"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def content_hash(data: str | bytes) -> str:
    """MD5 hash of the content (to detect changes)."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.md5(data).hexdigest()


class CrawlState:
    """
    Persistent dictionary: URL → {hash, last_crawled, title, file, changed}.
    """

    def __init__(self):
        CRAWL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._state: dict = {}
        self._load()

    def _load(self):
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE, encoding="utf-8") as f:
                    self._state = json.load(f)
                log.info(f"Stato caricato: {len(self._state)} URL tracciati")
            except (OSError, ValueError) as e:
                log.warning(f"Impossibile caricare lo stato: {e} — parto da zero")
                self._state = {}

    def save(self):
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(self._state, f, ensure_ascii=False, indent=2)

    def is_changed(self, url: str, new_hash: str) -> bool:
        """Return True if the content is new or changed since the last scan."""
        entry = self._state.get(url)
        if entry is None:
            return True  # never seen before
        return entry.get("hash") != new_hash

    def update(self, url: str, new_hash: str, title: str = "", file: str = ""):
        """Update the state for a URL."""
        was_new = url not in self._state
        self._state[url] = {
            "hash": new_hash,
            "last_crawled": _now_iso(),
            "title": title,
            "file": file,
            "changed": True,
        }
        return was_new

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
