"""
Numpy-based vector store — no external dependency to compile.
Saves embeddings and metadata to disk, runs cosine search in memory.

Supports hybrid search: cosine similarity (vectors) + BM25 (keyword),
combined through Reciprocal Rank Fusion (RRF).

Saving to disk: every change (upsert/removal) rewrites the three store
files (embeddings.npy, metadata.json, ids.json — over 1 GB with 240k
chunks). During a long indexing run use `deferred_saves()`: writes
are postponed to the periodic `checkpoint()` calls and to the exit from the
block, instead of a full rewrite for every document.
"""

import bisect
import contextlib
import hashlib
import json
import logging
import os
import re
import sys
import tempfile
import threading
import time
import unicodedata
from collections import Counter
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
from api.safefile import read_json_bounded
from config.settings import CRAWL_CACHE_DIR, RETRIEVAL_TOP_K, VECTOR_STORE_DIR, is_migrated_source
from indexer.titles import (
    LINK_TEXTS_FILE,
    MAX_PAGE_ATTACHMENTS,
    PAGE_ATTACHMENTS_FILE,
    display_title,
    is_weak,
    title_from_text,
    with_text_title,
)

log = logging.getLogger(__name__)

EMBEDDINGS_FILE = VECTOR_STORE_DIR / "embeddings.npy"
METADATA_FILE   = VECTOR_STORE_DIR / "metadata.json"
IDS_FILE        = VECTOR_STORE_DIR / "ids.json"
MANIFEST_FILE   = VECTOR_STORE_DIR / "manifest.json"   # size and SHA-256 of the three files above
_STORE_FILES = (EMBEDDINGS_FILE, METADATA_FILE, IDS_FILE)

CHECKPOINT_INTERVAL = 1800  # seconds between two saves in deferred mode

# In-memory state (loaded only once)
_embeddings: np.ndarray | None = None   # shape (N, D)
_metadata: list[dict] = []
_ids: list[str] = []
_id_to_idx: dict[str, int] = {}
_bm25 = None          # BM25 index, rebuilt (lazily) when the corpus changes
_bm25_dirty = False   # True if the corpus changed after the last build
_acts_index = None    # (number, year) -> rows of the acts with that number, built lazily (find_acts)
_versions = None      # source -> (year, latest year, latest source) of its series, built lazily (version_info)
_grants = None        # beneficiary -> grant cards, built lazily (find_grants)
_news = None          # dated first passages of the news items, by date, built lazily (news_followups)
_pdf_title_count: Counter | None = None  # /Title → number of PDFs using it (lazy)
_pdf_first_text: dict[str, str] = {}      # PDF source → text of the first chunk
_pdf_display: dict[str, str] = {}         # PDF source → title to display (cache)
_pdf_link_texts: dict[str, str] = {}      # PDF source → link text (from the crawler)
_copy_of: dict[str, int] | None = None   # source of a copy → index of a passage of the kept document (lazy)
_source_rows: dict[str, list[int]] | None = None   # source → its rows, in chunk order (lazy)
_page_attachments: dict[str, list[str]] | None = None   # page → PDFs it links (lazy, from the crawler)

# Deferred saving (see deferred_saves / checkpoint)
_deferred_depth = 0
# Serializes searches with reload(): swapping the in-memory store must not
# happen in the middle of a search.
_lock = threading.RLock()
_dirty = False        # in-memory changes not yet written to disk
_last_save_ts = 0.0
# Set when files exist but could not be read: the store in memory is then empty only by force, and
# writing it would destroy the files that are still there (restore a backup, or reset on purpose).
_load_error: str | None = None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _check_manifest() -> None:
    """The three files must be the ones the last complete save wrote. A save interrupted between
    two of them (crash, power cut) leaves files of different generations that happen to have
    compatible sizes: they would be paired silently. Stores written before the manifest existed have none."""
    if not MANIFEST_FILE.exists():
        return
    with open(MANIFEST_FILE, encoding="utf-8") as f:
        expected = json.load(f).get("files", {})
    for path in _STORE_FILES:
        want = expected.get(path.name)
        if want is None or os.path.getsize(path) != want["size"] or _sha256(path) != want["sha256"]:
            raise ValueError(f"{path.name} does not match the manifest: a save was interrupted? Restore a backup")


def _sanitize(embeddings: np.ndarray) -> np.ndarray:
    """Vectors are stored with unit length. A row that is not a number (a rare overflow of the model),
    or whose length is off (damage, tampering), would rank first for every question: such rows are
    neutralised (zero = never similar) or brought back to unit length."""
    if embeddings.ndim != 2 or embeddings.shape[0] == 0:
        return embeddings
    bad = ~np.isfinite(embeddings).all(axis=1)
    if bad.any():
        log.error(f"{int(bad.sum())} vectors with non-finite values neutralised")
        embeddings = embeddings.copy() if not embeddings.flags.writeable else embeddings
        embeddings[bad] = 0.0
    norms = np.linalg.norm(embeddings, axis=1)
    off = norms > 1.05
    if off.any():
        log.error(f"{int(off.sum())} vectors with a length above 1 brought back to unit length")
        embeddings = embeddings.copy() if not embeddings.flags.writeable else embeddings
        embeddings[off] /= norms[off][:, None]
    return embeddings


def _read_from_disk() -> tuple[np.ndarray, list[dict], list[str]] | None:
    """Reads the three store files. None if missing; exception if unreadable."""
    if not (EMBEDDINGS_FILE.exists() and METADATA_FILE.exists() and IDS_FILE.exists()):
        return None
    for attempt in range(3):
        before = _file_identities()
        _check_manifest()
        embeddings = np.load(str(EMBEDDINGS_FILE), allow_pickle=False)
        with open(METADATA_FILE, encoding="utf-8") as f:
            metadata = json.load(f)
        with open(IDS_FILE, encoding="utf-8") as f:
            ids = json.load(f)
        if _file_identities() == before:
            break
        # a sync renamed a new generation in while we were reading: the three files may not
        # belong to the same set any more, read them again
        log.warning("Vector store files changed while loading (attempt %d): reading again", attempt + 1)
    else:
        raise ValueError("store rewritten three times while loading")
    if not (len(metadata) == len(ids) == embeddings.shape[0]):
        raise ValueError(f"store incoerente: {embeddings.shape[0]} vettori, "
                         f"{len(metadata)} metadati, {len(ids)} id")
    return _sanitize(embeddings), metadata, ids


def _file_identities() -> tuple:
    """(inode, size, mtime) of the store files: a renamed-in generation changes them."""
    out = []
    for p in (EMBEDDINGS_FILE, METADATA_FILE, IDS_FILE, MANIFEST_FILE):
        try:
            st = os.stat(p)
            out.append((st.st_ino, st.st_size, st.st_mtime_ns))
        except OSError:
            out.append(None)
    return tuple(out)


def _ensure_loaded():
    global _embeddings, _metadata, _ids, _id_to_idx, _last_save_ts, _load_error, _copy_of
    if _embeddings is not None:
        return
    VECTOR_STORE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        loaded = _read_from_disk()
    except Exception as e:
        _load_error = str(e)
        log.exception("Vector store unreadable. Working with an empty one; the files on disk are NOT "
                      "overwritten (restore a backup, or reset on purpose)")
        loaded = None
    if loaded is None:
        _reset_state()
    else:
        _embeddings, _metadata, _ids = loaded
        _id_to_idx = {id_: i for i, id_ in enumerate(_ids)}
        dim = _embeddings.shape[1] if _embeddings.ndim == 2 else 0
        log.info(f"Vector store loaded: {len(_ids)} chunks, dim={dim}")
        _build_bm25()
        _copy_of = _find_copies(_metadata)
    _last_save_ts = time.time()


def reload() -> dict:
    """Reloads the store from disk while the process keeps answering:
    lets the API see the passages written by `scripts.sync news` without the
    restart (20-30 s of 502) that follows the weekly syncs. The new store and
    its BM25 are built separately and swapped under `_lock`, so no
    search sees a half-built state. If the files are unreadable the old one
    stays. Returns the stats with `reloaded` True/False."""
    global _embeddings, _metadata, _ids, _id_to_idx, _bm25, _bm25_dirty
    global _pdf_title_count, _pdf_first_text, _pdf_display, _pdf_link_texts, _last_save_ts, _copy_of
    global _source_rows, _page_attachments, _acts_index, _versions, _grants, _news
    try:
        loaded = _read_from_disk()
    except (OSError, ValueError, EOFError) as e:
        log.warning(f"Vector store reload failed, keeping the in-memory one: {e}")
        return {**get_stats(), "reloaded": False}
    if loaded is None:
        log.warning("Vector store reload: files missing, keeping the in-memory one")
        return {**get_stats(), "reloaded": False}
    embeddings, metadata, ids = loaded
    bm25 = _make_bm25(metadata)
    copies = _find_copies(metadata)
    with _lock:
        _embeddings, _metadata, _ids = embeddings, metadata, ids
        _id_to_idx = {id_: i for i, id_ in enumerate(_ids)}
        _bm25, _bm25_dirty = bm25, False
        _pdf_title_count, _pdf_first_text, _pdf_display, _pdf_link_texts = None, {}, {}, {}
        _copy_of = copies
        _source_rows, _page_attachments = None, None
        _acts_index = None
        _versions = None
        _grants = None
        _news = None
        _last_save_ts = time.time()
    log.info(f"Vector store reloaded: {len(_ids)} chunks")
    return {**get_stats(), "reloaded": True}


def _reset_state():
    global _embeddings, _metadata, _ids, _id_to_idx, _bm25, _bm25_dirty, _pdf_title_count, _copy_of, _source_rows
    global _acts_index, _versions, _grants, _news
    _acts_index = None
    _versions = None
    _grants = None
    _news = None
    _embeddings = None  # dimension determined by the first batch
    _metadata = []
    _ids = []
    _id_to_idx = {}
    _bm25 = None
    _bm25_dirty = False
    _pdf_title_count = None
    _copy_of = None
    _source_rows = None


def _tokenize(text: str) -> list[str]:
    """Simple tokenization for BM25: lowercase, words only."""
    return re.findall(r"[a-zàáâäèéêëìíîïòóôöùúûü]+", text.lower())


_MAX_QUERY_TERMS = 32


def _make_bm25(metadata: list[dict]):
    """BM25 index of the given corpus (None if empty or without rank_bm25)."""
    if not metadata:
        return None
    try:
        from rank_bm25 import BM25Okapi
        return BM25Okapi([_tokenize(m.get("text", "")) for m in metadata])
    except ImportError:
        log.warning("rank_bm25 not installed — hybrid search disabled. "
                    "Install with: pip install rank-bm25")
        return None


def _build_bm25():
    """Builds or rebuilds the BM25 index from the current corpus."""
    global _bm25, _bm25_dirty
    _bm25_dirty = False
    _bm25 = _make_bm25(_metadata)


def _ensure_bm25():
    """Rebuilds BM25 only if the corpus changed since the last build.
    Rebuilding (tokenizing the whole corpus) is expensive: do it
    once before a search, not at every save."""
    if _bm25_dirty:
        _build_bm25()


def _prepare(path: Path, write) -> str:
    """Writes the content to a temporary file created exclusively (random name, never a link that
    someone planted) in the same folder and returns its name. The index is public data that the
    API account must read: mode 644."""
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, "wb") as f:
            write(f)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    return tmp


def _atomic_replace(path: Path, write):
    """Writes through a temporary file, then renames it over `path` (atomic on all POSIX filesystems)."""
    tmp = _prepare(path, write)
    try:
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def write_manifest() -> None:
    """Records size and hash of the three files as they are now (for a script that rewrote one of them)."""
    manifest = {"files": {p.name: {"size": os.path.getsize(p), "sha256": _sha256(p)} for p in _STORE_FILES}}
    _atomic_replace(MANIFEST_FILE, lambda f: f.write(json.dumps(manifest).encode("utf-8")))


def _atomic_write_json(path: Path, data):
    """Writes JSON atomically (see _atomic_replace)."""
    _atomic_replace(path, lambda f: f.write(json.dumps(data, ensure_ascii=False).encode("utf-8")))


def _atomic_write_npy(path: Path, arr: np.ndarray):
    """Writes the array atomically (see _atomic_replace). np.save on an open file does not add
    the .npy extension."""
    _atomic_replace(path, lambda f: np.save(f, arr))


def _write_to_disk():
    global _dirty, _last_save_ts
    VECTOR_STORE_DIR.mkdir(parents=True, exist_ok=True)
    if _load_error is not None:
        raise RuntimeError(f"the vector store on disk could not be read ({_load_error}): not overwriting it. "
                           "Restore a backup, or empty it on purpose (clear_collection)")
    arr = _embeddings if _embeddings is not None else np.zeros((0, 0), dtype=np.float32)
    # Everything is written aside first, then the files are renamed one after the other as fast as
    # possible and the manifest last: a crash can only fall in those few renames, and the manifest
    # then tells at the next load that the set is not whole.
    contents = (
        (EMBEDDINGS_FILE, lambda f: np.save(f, arr)),
        (METADATA_FILE, lambda f: f.write(json.dumps(_metadata, ensure_ascii=False).encode("utf-8"))),
        (IDS_FILE, lambda f: f.write(json.dumps(_ids, ensure_ascii=False).encode("utf-8"))),
    )
    prepared: list[tuple[str, Path]] = []
    from api.filelock import file_lock
    lock = None
    try:
        lock = file_lock(MANIFEST_FILE)   # two writers (a sync and a script run by hand) never interleave
        lock.__enter__()
        for final, write in contents:
            prepared.append((_prepare(final, write), final))
        manifest = {"files": {final.name: {"size": os.path.getsize(tmp), "sha256": _sha256(Path(tmp))}
                              for tmp, final in prepared}}
        prepared.append((_prepare(MANIFEST_FILE, lambda f: f.write(json.dumps(manifest).encode("utf-8"))),
                         MANIFEST_FILE))
        for tmp, final in prepared:
            os.replace(tmp, final)
        prepared = []
    finally:
        if lock is not None:
            with contextlib.suppress(OSError):
                lock.__exit__(None, None, None)
        for tmp, _ in prepared:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
    _dirty = False
    _last_save_ts = time.time()


def _save():
    """Persists the state. Inside `deferred_saves()` it only marks the store as
    modified: the write happens at the checkpoint or on exit from the block."""
    global _dirty
    if _deferred_depth > 0:
        _dirty = True
        return
    _write_to_disk()


@contextmanager
def deferred_saves():
    """Postpones disk writes for the whole duration of the block.
    On exit (even on exception) it writes if there are pending changes."""
    global _deferred_depth
    _deferred_depth += 1
    try:
        yield
    finally:
        _deferred_depth -= 1
        if _deferred_depth == 0 and _dirty:
            _write_to_disk()


def checkpoint_due() -> bool:
    """True if CHECKPOINT_INTERVAL has elapsed since the last save."""
    return time.time() - _last_save_ts >= CHECKPOINT_INTERVAL


def checkpoint(force: bool = False) -> bool:
    """Writes pending changes to disk if `force` or if the interval has
    elapsed. Returns True if it wrote."""
    if not _dirty:
        return False
    if force or checkpoint_due():
        _write_to_disk()
        return True
    return False


def chunk_id(text: str, source: str, chunk_index: int) -> str:
    raw = f"{source}::{chunk_index}::{text[:64]}"
    return hashlib.md5(raw.encode()).hexdigest()


def upsert_chunks(chunks: list[dict], embeddings: list[list[float] | None]) -> int:
    """
    Inserts or updates chunks in the vector store.
    chunks: list of {"text": str, "metadata": dict}
    embeddings: one vector per chunk. `None` means "chunk already present
    with the same text": only the metadata is updated, the vector stays the one
    in memory (avoids calling Ollama again for unchanged content).
    Returns the number of chunks inserted or updated with a new vector.
    """
    global _embeddings, _bm25_dirty, _pdf_title_count, _copy_of, _source_rows, _acts_index, _versions, _grants, _news

    if not chunks:
        return 0

    _ensure_loaded()

    inserted = 0
    updated = 0
    refreshed = 0
    new_vecs = []
    new_meta = []
    new_ids  = []

    for chunk, vec in zip(chunks, embeddings):
        # Sanitize metadata: bytes is not JSON-serializable
        chunk["metadata"] = {
            k: v.decode("utf-8", errors="replace") if isinstance(v, bytes) else v
            for k, v in chunk["metadata"].items()
        }
        cid = chunk_id(
            chunk["text"],
            chunk["metadata"].get("source", ""),
            chunk["metadata"].get("chunk_index", 0),
        )
        meta = {**chunk["metadata"], "text": chunk["text"]}

        if vec is None:
            idx = _id_to_idx.get(cid)
            if idx is None:
                log.warning(f"  Chunk {cid} has no embedding and is not in the store, ignored")
                continue
            if _metadata[idx].get("needs_reembedding"):
                meta["needs_reembedding"] = True
            _metadata[idx] = meta
            refreshed += 1
            continue

        norm_vec = np.array(vec, dtype=np.float32)
        n = np.linalg.norm(norm_vec)
        if n > 0:
            norm_vec = norm_vec / n
            meta.pop("needs_reembedding", None)
        else:
            meta["needs_reembedding"] = True

        if cid in _id_to_idx:
            # Update in place
            idx = _id_to_idx[cid]
            _embeddings[idx] = norm_vec
            _metadata[idx] = meta
            updated += 1
        else:
            new_vecs.append(norm_vec)
            new_meta.append(meta)
            new_ids.append(cid)
            inserted += 1

    if new_vecs:
        new_arr = np.stack(new_vecs, axis=0)
        if _embeddings is None or _embeddings.shape[0] == 0:
            _embeddings = new_arr
        elif _embeddings.shape[1] != new_arr.shape[1]:
            raise ValueError(
                f"Dimensione embedding inconsistente: store={_embeddings.shape[1]}, "
                f"nuovo batch={new_arr.shape[1]}. "
                f"Svuotare il vector store con: rm -rf data/vectorstore/"
            )
        else:
            _embeddings = np.concatenate([_embeddings, new_arr], axis=0)
        start_idx = len(_ids)
        _ids.extend(new_ids)
        _metadata.extend(new_meta)
        for i, cid in enumerate(new_ids):
            _id_to_idx[cid] = start_idx + i

    if inserted or updated or refreshed:
        _bm25_dirty = True
        _pdf_title_count = None
        _copy_of = None
        _source_rows = None
        _acts_index = None
        _versions = None
        _grants = None
        _news = None
        _save()
    return inserted + updated


def chunks_to_embed(chunks: list[dict], ids: list[str]) -> list[bool]:
    """For each chunk says whether the embedding must be (re)computed: id absent
    from the store, text different from the stored one, or zero vector
    (embedding failed in a previous sync)."""
    _ensure_loaded()
    flags = []
    for chunk, cid in zip(chunks, ids):
        idx = _id_to_idx.get(cid)
        if idx is None or _metadata[idx].get("text") != chunk["text"] or not np.any(_embeddings[idx]):
            flags.append(True)
        else:
            flags.append(False)
    return flags


def _is_inbox(meta: dict) -> bool:
    """Chunk loaded by hand from the inbox (scripts/inbox_indexer): it must be kept
    even if its source does not appear in the crawl or is re-crawled.
    A "document" without any origin is an inbox document that predates the marker; one with
    origin=news is an Office attachment of a news item, which is replaced like any other news chunk."""
    return meta.get("origin") == "inbox" or (meta.get("doc_type") == "document" and not meta.get("origin"))


def _is_protected(meta: dict) -> bool:
    """Chunks that the crawl does not know and that syncs must not remove as
    «stale»: inbox documents, Bollettino news indexed by `scripts.news_sync`
    (origin=news; their domain is not in the crawl), the transparency documents of
    `scripts.amt_sync` (origin=amt) and the acts indexed by
    `scripts.acts_sync` (origin=acts; the registers are search results, not pages).
    Unlike the inbox, they remain replaceable by their own indexer when the text
    changes (see source_chunk_ids)."""
    return _is_inbox(meta) or meta.get("origin") in ("news", "acts", "amt")


def source_chunk_ids(source: str, include_inbox: bool = False) -> set[str]:
    """Returns the ids of the chunks currently in the store for a source.
    Inbox chunks are excluded by default: the indexer would treat them as "stale"
    when it re-indexes the crawled page with the same URL."""
    _ensure_loaded()
    return {
        _ids[i] for i, m in enumerate(_metadata)
        if m.get("source") == source and (include_inbox or not _is_inbox(m))
    }


def inbox_sources() -> set[str]:
    """Sources of the chunks loaded from the inbox."""
    _ensure_loaded()
    return {m.get("source", "") for m in _metadata if _is_inbox(m) and m.get("source")}


# A /Title used by at least this many different PDFs is a header, not a title
_REPEATED_TITLE_MIN = 5


# The link texts are written by the sync account and read here, under the search lock: the file is
# read without following a link, refused if it is not a regular file (a FIFO would block every
# search) or bigger than this, and only string pairs are kept.
_LINK_TEXTS_MAX_BYTES = 32 * 1024 * 1024


def _load_link_texts() -> dict[str, str]:
    """Link texts pointing to PDFs saved by the crawler (see crawler.save_link_texts)."""
    path = CRAWL_CACHE_DIR / LINK_TEXTS_FILE
    try:
        data = read_json_bounded(path, _LINK_TEXTS_MAX_BYTES, dict)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        log.warning(f"{path} unreadable: PDF titles without the link texts")
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}


def _title(meta: dict) -> str:
    """Title to display: for crawled PDFs a readable one (see indexer/titles.py).
    The reranking in api/rag.py still uses `title`, so the search does not change."""
    global _pdf_title_count, _pdf_first_text, _pdf_display, _pdf_link_texts
    title = meta.get("title", "")
    if meta.get("doc_type") != "pdf" or _is_inbox(meta):
        return title
    if _pdf_title_count is None:
        titles, first_text = {}, {}
        for m in _metadata:
            if m.get("doc_type") == "pdf" and not _is_inbox(m):
                src = m.get("source", "")
                titles.setdefault(src, m.get("title", ""))
                if m.get("chunk_index") == 0:
                    first_text[src] = m.get("text", "")
        _pdf_title_count = Counter(titles.values())
        _pdf_first_text, _pdf_display = first_text, {}
        _pdf_link_texts = _load_link_texts()
    source = meta.get("source", "")
    if source not in _pdf_display:
        shown = display_title(title, source,
                              repeated=_pdf_title_count[title] >= _REPEATED_TITLE_MIN,
                              link_text=_pdf_link_texts.get(source, ""))
        if is_weak(shown):  # «Allegato B 2022», «DD 665-25»: look in the text
            shown = with_text_title(shown, title_from_text(_pdf_first_text.get(source, ""), title))
        _pdf_display[source] = shown
    return _pdf_display[source]


def _body(meta: dict) -> str:
    """A passage's text without the title line the indexer puts first: for a crawled PDF that is
    the cached file name, different for every copy of the same file."""
    text = meta.get("text", "").strip()
    title = meta.get("title", "")
    if title and text.startswith(title + "\n"):
        text = text[len(title):].lstrip()
    return text


def _kept_first(source: str) -> tuple:
    """Which address of a document to keep: not on a retired domain, without /index.php/ or a
    query string, on the www host, then the shortest."""
    return (is_migrated_source(source), "/index.php/" in source, "?" in source,
            not source.startswith("https://www."), len(source), source)


def _find_copies(metadata: list[dict]) -> dict[str, int]:
    """The same file published at several addresses (a regulation in two folders, the same PDF
    as 2-x.pdf and 6-x.pdf): documents whose passages are all identical. For each copy, the index
    of a passage of the document that is kept (see _kept_first()). Without this, the copies
    filled several places of the context with one document under different names. The
    administrator's inbox documents are left alone."""
    texts: dict[str, list[tuple[int, str]]] = {}
    first: dict[str, int] = {}
    for i, m in enumerate(metadata):
        src = m.get("source", "")
        if not src or _is_inbox(m):
            continue
        texts.setdefault(src, []).append((m.get("chunk_index", 0) or 0, _body(m)))
        first.setdefault(src, i)
    groups: dict[str, list[str]] = {}
    for src, parts in texts.items():
        parts.sort()
        digest = hashlib.sha256("\x00".join(t for _, t in parts).encode()).hexdigest()
        groups.setdefault(digest, []).append(src)
    copies: dict[str, int] = {}
    for sources in groups.values():
        if len(sources) < 2:
            continue
        kept = min(sources, key=_kept_first)
        for src in sources:
            if src != kept:
                copies[src] = first[kept]
    return copies


def _kept_meta(meta: dict) -> dict:
    """The passage's metadata, or that of the kept document when the passage is from a copy."""
    global _copy_of
    if _copy_of is None:
        _copy_of = _find_copies(_metadata)
    idx = _copy_of.get(meta.get("source", ""))
    return meta if idx is None else _metadata[idx]


def search(query_embedding: list[float], top_k: int = RETRIEVAL_TOP_K) -> list[dict]:
    """
    Finds the most similar chunks via cosine similarity.
    Returns a list of {"text", "source", "title", "score"}.
    """
    with _lock:
        return _search(query_embedding, top_k)


def _search(query_embedding: list[float], top_k: int) -> list[dict]:
    _ensure_loaded()

    if _embeddings is None or len(_ids) == 0:
        log.warning("Vector store is empty. Run the indexing first.")
        return []

    q = np.array(query_embedding, dtype=np.float32)
    n = np.linalg.norm(q)
    if n > 0:
        q = q / n

    # Cosine similarity = dot product (vectors already normalized)
    scores = _embeddings @ q  # shape (N,)

    k = min(top_k, len(_ids))
    top_indices = np.argpartition(scores, -k)[-k:]
    top_indices = top_indices[np.argsort(scores[top_indices])[::-1]]

    results = []
    for idx in top_indices:
        meta = _metadata[idx]
        results.append({
            "text":     meta.get("text", ""),
            "source":   meta.get("source", ""),
            "title":    meta.get("title", ""),
            "display_title": _title(meta),
            "doc_type": meta.get("doc_type", ""),
            "score":    float(scores[idx]),
        })

    return results


def hybrid_search(
    query_embedding: list[float],
    query_text: str,
    top_k: int = RETRIEVAL_TOP_K,
    vector_weight: float = 0.6,
    bm25_weight: float = 0.4,
) -> list[dict]:
    """
    Hybrid search: combines cosine similarity and BM25 via weighted RRF.

    vector_weight + bm25_weight = 1.0 (recommended)
    """
    with _lock:
        return _hybrid_search(query_embedding, query_text, top_k, vector_weight, bm25_weight)


def _hybrid_search(query_embedding, query_text, top_k, vector_weight, bm25_weight) -> list[dict]:
    _ensure_loaded()

    if _embeddings is None or len(_ids) == 0:
        log.warning("Vector store is empty.")
        return []

    _ensure_bm25()

    n_docs = len(_ids)
    fetch_k = min(top_k * 20, n_docs)  # fetch more candidates for the fusion

    # --- Vector search ---
    q = np.array(query_embedding, dtype=np.float32)
    norm = np.linalg.norm(q)
    if norm > 0:
        q = q / norm
    vec_scores = _embeddings @ q

    vec_top = np.argpartition(vec_scores, -fetch_k)[-fetch_k:]
    vec_top = vec_top[np.argsort(vec_scores[vec_top])[::-1]]

    # Vector rank: position → RRF score
    vec_rrf = {}
    for rank, idx in enumerate(vec_top):
        vec_rrf[int(idx)] = 1.0 / (60 + rank + 1)

    # --- BM25 search ---
    bm25_rrf = {}
    if _bm25 is not None:
        # each word once, and no more than a few dozen: the scoring loops over every passage for each
        # word, so a question made of hundreds of words would keep the whole service busy for seconds
        tokens = list(dict.fromkeys(_tokenize(query_text)))[:_MAX_QUERY_TERMS]
        if tokens:
            bm25_scores = _bm25.get_scores(tokens)
            bm25_top = np.argpartition(bm25_scores, -fetch_k)[-fetch_k:]
            bm25_top = bm25_top[np.argsort(bm25_scores[bm25_top])[::-1]]
            for rank, idx in enumerate(bm25_top):
                bm25_rrf[int(idx)] = 1.0 / (60 + rank + 1)

    # --- Weighted fusion ---
    all_indices = set(vec_rrf) | set(bm25_rrf)
    fused = {}
    for idx in all_indices:
        fused[idx] = (
            vector_weight * vec_rrf.get(idx, 0.0) +
            bm25_weight  * bm25_rrf.get(idx, 0.0)
        )

    # The same passage is often indexed under several addresses of one page (www and bare host,
    # /index.php/, ?type=3): only its best copy counts, otherwise the copies fill every place and
    # the other pages never reach the context
    results = []
    seen_texts: set[str] = set()
    for idx in sorted(fused, key=fused.__getitem__, reverse=True):
        meta = _metadata[idx]
        text = _body(meta)
        if text in seen_texts:
            continue
        seen_texts.add(text)
        doc = _kept_meta(meta)   # a copy of a document is listed as the document itself
        results.append({
            "text":     meta.get("text", ""),
            "source":   doc.get("source", ""),
            "title":    doc.get("title", ""),
            "display_title": _title(doc),
            "doc_type": meta.get("doc_type", ""),
            "category": meta.get("category", ""),
            "date":     meta.get("date", ""),   # shown to the model (build_context_block)
            "score":    float(vec_scores[idx]),  # semantic score for the MIN_SIMILARITY filter
        })
        if len(results) == top_k:
            break

    return results


def _rows_by_source() -> dict[str, list[int]]:
    global _source_rows
    if _source_rows is None:
        rows: dict[str, list[int]] = {}
        for i, meta in enumerate(_metadata):
            rows.setdefault(meta.get("source", ""), []).append(i)
        for r in rows.values():
            r.sort(key=lambda i: _metadata[i].get("chunk_index", 0))
        _source_rows = rows
    return _source_rows


def _attachments_of_pages() -> dict[str, list[str]]:
    """Page → PDFs it links, as saved by the crawler; pages linking too many are left out."""
    global _page_attachments
    if _page_attachments is None:
        path = CRAWL_CACHE_DIR / PAGE_ATTACHMENTS_FILE
        try:
            data = read_json_bounded(path, _LINK_TEXTS_MAX_BYTES, dict)
        except FileNotFoundError:
            data = {}
        except (OSError, ValueError):
            log.warning(f"{path} unreadable: no passages from the attachments of the pages")
            data = {}
        _page_attachments = {
            page: [u for u in pdfs if isinstance(u, str)]
            for page, pdfs in data.items()
            if isinstance(page, str) and isinstance(pdfs, list) and 0 < len(pdfs) <= MAX_PAGE_ATTACHMENTS
        }
    return _page_attachments


def attachment_passages(pages: list[str], query_embedding: list[float], limit: int = 2,
                        min_score: float = 0.0, skip_sources: set[str] | frozenset = frozenset()) -> list[dict]:
    """The passages of the PDFs attached to the given pages that are closest to the query: the best
    passage of each attachment, joined to the passage that follows it (a sentence or a table row cut
    in two by the chunker), the `limit` best overall, above `min_score` (cosine). A page that cites
    a regulation or a fee table brings the part of it that answers, which the search over the whole
    index does not always find."""
    with _lock:
        _ensure_loaded()
        if _embeddings is None or not _ids or limit <= 0:
            return []
        attached = _attachments_of_pages()
        if not attached:
            return []
        q = np.array(query_embedding, dtype=np.float32)
        norm = np.linalg.norm(q)
        if norm == 0:
            return []
        q = q / norm
        rows = _rows_by_source()
        best: list[tuple[float, int, str]] = []
        seen: set[str] = set()
        for page in pages:
            for pdf in attached.get(page, []):
                if pdf in seen or pdf in skip_sources or pdf not in rows:
                    continue
                seen.add(pdf)
                scores = _embeddings[rows[pdf]] @ q
                j = int(np.argmax(scores))
                best.append((float(scores[j]), j, pdf))
        best.sort(key=lambda b: b[0], reverse=True)
        out = []
        for score, j, pdf in best[:limit]:
            if score < min_score:
                break
            r = rows[pdf]
            meta = _metadata[r[j]]
            text = meta.get("text", "")
            if j + 1 < len(r):
                text += "\n" + _body(_metadata[r[j + 1]])
            doc = _kept_meta(meta)
            out.append({
                "text":     text,
                "source":   doc.get("source", ""),
                "title":    doc.get("title", ""),
                "display_title": _title(doc),
                "doc_type": meta.get("doc_type", ""),
                "category": meta.get("category", ""),
                "date":     meta.get("date", ""),
                "score":    score,
            })
        return out

# An act (a determination, a resolution, an order) is asked for by its number: «cosa dice la determina
# n. 1200 del 2025?». The BM25 side of the search keeps only letters, and a card found by its number
# alone would not pass the semantic threshold anyway: the act is looked up here by number and year.
# Two forms are recognised in the first passage of a document: the cards and documents of the
# transparency job («Numero: 1200/2025; Data: 05/11/2026» or «Numero: 161; Data: 05/11/2026») and
# the titles of the acts job («Delibera di Giunta comunale n. 293 del 31/12/2025: …»).
_ACT_NUMBER_RE = re.compile(r"\bNumero: (\d{1,6})(?:/(\d{4}))?; Data: \d\d/\d\d/(\d{4})\b")
_ACT_TITLE_RE = re.compile(r"\bn\.\s*(\d{1,6})\s+del\s+\d\d/\d\d/(\d{4})\b")
_ACT_KINDS = (("determina", "determin"), ("ordinanza", "ordinanz"), ("delibera", "deliber"),
              ("decreto", "decret"))
_ACT_HEAD = 800   # characters of the first passage that hold the number, the date and the type


def _act_kind(head: str, title: str) -> str:
    """«determina», «ordinanza», «delibera», «decreto» or '' (unknown): read from the type of the
    document, else from its section, else from its title (the acts job). The title alone would mislead:
    a determination may be called «Annullamento ordinanza dirigenziale n. 473»."""
    m = re.search(r"Tipo Documento: ([^;\n]+)", head)
    probe = m[1] if m else ""
    if not probe:
        m = re.search(r"sezione ([^:\n]+):", head)
        probe = m[1] if m else title[:40]
    low = probe.lower()
    for kind, stem in _ACT_KINDS:
        if stem in low:
            return kind
    return ""


def _build_acts_index(metadata: list[dict]) -> dict[tuple[int, int], list[tuple[int, str]]]:
    index: dict[tuple[int, int], list[tuple[int, str]]] = {}
    for i, meta in enumerate(metadata):
        if meta.get("chunk_index", 0) != 0:
            continue
        head = meta.get("text", "")[:_ACT_HEAD]
        title = meta.get("title", "")
        m = _ACT_NUMBER_RE.search(head)
        if m:
            number, year = int(m[1]), int(m[2] or m[3])
        else:
            m = _ACT_TITLE_RE.search(title)
            if not m:
                continue
            number, year = int(m[1]), int(m[2])
        index.setdefault((number, year), []).append((i, _act_kind(head, title)))
    return index


def find_acts(number: int, year: int, kind: str = "", limit: int = 4) -> list[dict]:
    """The acts with that number and year, as search results (score 1.0). `kind` («determina»,
    «ordinanza», «delibera», «decreto») keeps only the acts of that kind; '' keeps them all."""
    global _acts_index
    with _lock:
        _ensure_loaded()
        if _acts_index is None:
            _acts_index = _build_acts_index(_metadata)
            log.info(f"Acts by number: {len(_acts_index)} numbers")
        rows = _acts_index.get((number, year), [])
        if kind:
            rows = [r for r in rows if r[1] == kind]
        results = []
        for idx, _ in rows[:limit]:
            meta = _metadata[idx]
            results.append({
                "text":     meta.get("text", ""),
                "source":   meta.get("source", ""),
                "title":    meta.get("title", ""),
                "display_title": _title(meta),
                "doc_type": meta.get("doc_type", ""),
                "category": meta.get("category", ""),
                "date":     meta.get("date", ""),
                "score":    1.0,
            })
        return results


# «Cosa dice l'articolo 18 del regolamento?»: the search picks the regulation, but the passage with
# that article is a matter of a number, which neither the words nor the meaning of the question carry.
_ARTICLE_HEADING = r"(?im)^[ \t]*art(?:icolo|\.)?[ \t]*{n}(?![\d.,])"
_ANY_HEADING = re.compile(r"(?im)^[ \t]*art(?:icolo|\.)?[ \t]*\d{1,3}(?![\d.,])")


def article_passages(sources: list[str], number: int, limit: int = 2) -> list[dict]:
    """The passage where article `number` starts, in each of the given sources (pages, PDFs) and in
    the PDFs attached to the pages among them, joined to the passage that follows (an article is often
    cut in two by the chunker). The first `limit` sources that have the article, in the given order."""
    heading = re.compile(_ARTICLE_HEADING.format(n=number))
    with _lock:
        _ensure_loaded()
        rows = _rows_by_source()
        attached = _attachments_of_pages()
        todo: list[str] = []
        for src in sources:
            for s in [src, *attached.get(src, [])]:
                if s and s not in todo:
                    todo.append(s)
        out = []
        for src in todo:
            r = rows.get(src, [])
            # the table of contents lists every article too: among the passages with the heading, the
            # one with the fewest headings is the body of the article, not the index
            found = [k for k, i in enumerate(r) if heading.search(_metadata[i].get("text", ""))]
            if not found:
                continue
            j = min(found, key=lambda k: (len(_ANY_HEADING.findall(_metadata[r[k]].get("text", ""))), k))
            meta = _metadata[r[j]]
            text = meta.get("text", "")
            if j + 1 < len(r):
                text += "\n" + _body(_metadata[r[j + 1]])
            doc = _kept_meta(meta)
            out.append({
                "text":     text,
                "source":   doc.get("source", ""),
                "title":    doc.get("title", ""),
                "display_title": _title(doc),
                "doc_type": meta.get("doc_type", ""),
                "category": meta.get("category", ""),
                "date":     meta.get("date", ""),
                "score":    1.0,
            })
            if len(out) == limit:
                break
        return out


# A transparency section (and a page of the sites) publishes the same document year after year («Compensi degli amministratori
# anno 2022» … «anno 2025», «Piano prevenzione corruzione 2018/2020» … «2022/2024»): the versions
# differ by a year in the title, and the search picks one of them at random, often an old one.
_VERSION_YEAR_RE = re.compile(r"\b(20[0-4]\d)\b")
_VERSION_STRIP_RE = re.compile(r"[^a-zà-ü ]+")   # numbers, dates and punctuation: «n. 19 del 23.02.2026», «N°53»


def _series_key(meta: dict) -> tuple[str, str] | None:
    """(section, title without its numbers and punctuation) of a document with a year in its title,
    else None."""
    title = meta.get("title", "")
    if not _VERSION_YEAR_RE.search(title):
        return None
    source = meta.get("source", "")
    section = re.search(r"[?&]voceamt=(\d+)", source)
    where = section[1] if section else source.rsplit("/", 1)[0]
    return where, re.sub(r"\s+", " ", _VERSION_STRIP_RE.sub(" ", title.lower())).strip()


def _versioned(meta: dict) -> bool:
    """A document that can be one of several yearly versions: a transparency document (not a card),
    or a web page of the sites («2026, censimento permanente della popolazione (annuale)» … «2018, …»).
    News items, acts and the inbox are not: an old news item is the memory of what happened that year."""
    if meta.get("chunk_index", 0) != 0 or meta.get("card"):
        return False
    origin = meta.get("origin")
    if origin == "amt":
        return True
    return origin in (None, "", "web") and meta.get("doc_type") == "html" and "/news" not in meta.get("source", "")


def _build_versions(metadata: list[dict]) -> dict[str, tuple[int, int, str]]:
    series: dict[tuple[str, str], dict[str, int]] = {}
    web: set[tuple[str, str]] = set()
    for meta in metadata:
        if not _versioned(meta):
            continue
        key = _series_key(meta)
        if key is None:
            continue
        year = max(int(y) for y in _VERSION_YEAR_RE.findall(meta.get("title", "")))
        series.setdefault(key, {})[meta.get("source", "")] = year
        if meta.get("origin") != "amt":
            web.add(key)
    out: dict[str, tuple[int, int, str]] = {}
    for key, docs in series.items():
        if len(docs) < 2:
            continue
        if key in web and len(set(docs.values())) < len(docs):
            continue   # web pages with the same year are different pages («verbale n. 3 del …»), not versions
        latest_source = max(docs, key=lambda src: (docs[src], src))
        latest = docs[latest_source]
        for source, year in docs.items():
            out[source] = (year, latest, latest_source)
    return out


def _ensure_versions() -> dict[str, tuple[int, int, str]]:
    global _versions
    if _versions is None:
        _versions = _build_versions(_metadata)
        log.info(f"Yearly versions: {len(_versions)} documents in series")
    return _versions


def version_info(source: str) -> tuple[int, int] | None:
    """(year of this version, year of the latest version of the same document) when the source is
    one of several yearly versions of a transparency document or of a web page; None otherwise."""
    with _lock:
        _ensure_loaded()
        v = _ensure_versions().get(source)
        return (v[0], v[1]) if v else None


def latest_version_passages(chosen: list[dict], query_embedding: list[float], limit: int = 2) -> list[dict]:
    """When the search picked an older yearly version of a document and the latest version is not
    among the passages chosen, the passage of the latest version closest to the question: the
    penalty in the re-ranking cannot lift a version the search did not bring at all (a plan of
    1,100 passages). The `limit` best overall."""
    with _lock:
        _ensure_loaded()
        if _embeddings is None or not _ids or limit <= 0:
            return []
        q = np.array(query_embedding, dtype=np.float32)
        norm = np.linalg.norm(q)
        if norm == 0:
            return []
        q = q / norm
        versions = _ensure_versions()
        rows = _rows_by_source()
        have = {c.get("source", "") for c in chosen}
        best: list[tuple[float, int]] = []
        seen: set[str] = set()
        for c in chosen:
            v = versions.get(c.get("source", ""))
            if not v or v[0] >= v[1] or v[2] in have or v[2] in seen or v[2] not in rows:
                continue
            seen.add(v[2])
            r = rows[v[2]]
            scores = _embeddings[r] @ q
            j = int(np.argmax(scores))
            best.append((float(scores[j]), r[j]))
        best.sort(key=lambda b: b[0], reverse=True)
        return [_result(idx, score) for score, idx in best[:limit]]


# «Il sindaco ha revocato l'ordinanza…», «il mare è di nuovo balneabile», «riaperta la strada»: a news
# item that ends what an earlier one announced.
_FOLLOWUP_RE = re.compile(r"\b(revoc\w+|riapert\w+|riapr\w+|di nuovo|ripristin\w+|cessat\w+|terminat\w+|"
                          r"annullat\w+|rientrat\w+|non (?:è|sono) più)\b", re.IGNORECASE)
_FOLLOWUP_DAYS = 90      # a follow-up is looked for in the news of the days after the item
_FOLLOWUP_HEAD = 700     # characters of the follow-up that must name what it ends
_FOLLOWUP_WORDS = 3      # words of the title of the item (five letters or more) the follow-up must share


def _news_words(text: str) -> frozenset[str]:
    return frozenset(t for t in (_plain(w) for w in re.findall(r"[A-Za-zÀ-ü]{5,}", text)) if t not in _GRANT_STOPWORDS)


def _build_news(metadata: list[dict]) -> list[tuple[str, int]]:
    """(date, row) of the first passage of every dated news item, in order of date."""
    rows = []
    for i, meta in enumerate(metadata):
        if meta.get("chunk_index", 0) != 0 or meta.get("category") != "notizia":
            continue
        day = str(meta.get("date", ""))[:10]
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            rows.append((day, i))
    rows.sort()
    return rows


def news_followups(chosen: list[dict], limit: int = 2) -> dict[str, dict]:
    """For the news items among `chosen` that a later news item ends (a ban revoked, a road reopened),
    that later item: {source of the item: passage of its follow-up}. The search finds the ban, which
    looks like the question, and not its revocation («the sea is bathable again»), so the answer
    gave a ban of months before as possibly still in force. A follow-up is a news item of the
    following `_FOLLOWUP_DAYS` days whose beginning says that something ended and shares at least
    `_FOLLOWUP_WORDS` words with the title of the item; the nearest in time, at most `limit` in all."""
    global _news
    out: dict[str, dict] = {}
    with _lock:
        _ensure_loaded()
        if _news is None:
            _news = _build_news(_metadata)
            log.info(f"News follow-ups: {len(_news)} dated news items")
        if not _news:
            return out
        have = {c.get("source", "") for c in chosen}
        for c in chosen:
            day = str(c.get("date", ""))[:10]
            if len(out) >= limit or c.get("category") != "notizia" or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                continue
            words = _news_words(c.get("title", ""))
            if len(words) < _FOLLOWUP_WORDS:
                continue
            try:
                last = (date.fromisoformat(day) + timedelta(days=_FOLLOWUP_DAYS)).isoformat()
            except ValueError:
                continue
            for k in range(bisect.bisect_right(_news, (day, len(_metadata))), len(_news)):
                when, i = _news[k]
                if when > last:
                    break
                meta = _metadata[i]
                doc = _kept_meta(meta)
                src = doc.get("source", "")
                if src in have or src == c.get("source"):
                    continue
                head = f"{doc.get('title', '')} {meta.get('text', '')[:_FOLLOWUP_HEAD]}"
                if _FOLLOWUP_RE.search(head) and len(words & _news_words(head)) >= _FOLLOWUP_WORDS:
                    out[c.get("source", "")] = _result(i, float(c.get("score", 0.0)))
                    have.add(src)
                    break
    return out


def _result(idx: int, score: float) -> dict:
    meta = _metadata[idx]
    doc = _kept_meta(meta)
    return {
        "text":     meta.get("text", ""),
        "source":   doc.get("source", ""),
        "title":    doc.get("title", ""),
        "display_title": _title(doc),
        "doc_type": meta.get("doc_type", ""),
        "category": meta.get("category", ""),
        "date":     meta.get("date", ""),
        "score":    score,
    }


# «Quali contributi ha ricevuto la Croce Rossa?»: the cards of the grants section name the beneficiary
# («Beneficiario: CROCE ROSSA ITALIANA COMITATO LOCALE …; Importo: 10.000,00 €; … Data atto: …»), but
# a name is drowned among the hundreds of acts that mention it. The cards are looked up by the words
# of the beneficiary's name that tell it apart (not «associazione», «srl», the name of the town).
_GRANT_RE = re.compile(r"\bBeneficiario: ([^;\n]+); Importo: ([^;\n]*)")
_GRANT_DATE_RE = re.compile(r"\b(?:Data atto|Pubblicato dal): (\d\d)/(\d\d)/(\d{4})")
_GRANT_AMOUNT_RE = re.compile(r"-?\d{1,3}(?:\.\d{3})*(?:,\d+)?")
_GRANT_STOPWORDS = frozenset([
    "associazione", "associazioni", "culturale", "culturali", "del", "della", "dei", "delle", "degli",
    "dell", "dello", "sociale", "sociali", "san", "santa", "asd", "comune", "comunale", "comunali", "srl",
    "srls", "coop", "cooperativa", "cooperative", "soc", "aps", "onlus", "sportiva", "sportive", "sportivo",
    "piceno", "marche", "societa", "comitato", "ascoli", "dilettantistica", "dilettantistiche", "circolo",
    "odv", "fondazione", "italiana", "italiano", "italia", "istituto", "insieme", "centro", "per", "hotel",
    "unione", "club", "provinciale", "nazionale", "regionale", "snc", "ets", "sbt", "sas", "sport",
    "consorzio", "polisportiva", "ass", "universita", "con", "cons", "spa", "locale", "sezione", "gruppo",
    "ente", "parrocchia", "scuola", "comprensivo", "pro", "loco", "amici", "condominio", "tesoreria",
    "stato", "dello", "benedetto", "tronto", "volontariato", "volontari", "organizzazione", "cultura", "arl",
    "pubblica", "privata", "impresa", "ditta", "studio", "sig", "sigra", "dott", "dottssa",
])
_GRANT_MAX_BENEFICIARIES = 10   # more names matched: the words are too generic, the search does better
_GRANT_RARE_WORD = 3            # a single word of the name is enough when at most this many beneficiaries have it
_GRANT_RARE_WORD_LEN = 5        # … and it is this long: «mare», «sole», «casa» are in three names and in any question


def _plain(word: str) -> str:
    return unicodedata.normalize("NFKD", word).encode("ascii", "ignore").decode().lower()


def _grant_tokens(name: str) -> frozenset[str]:
    return frozenset(t for t in (_plain(w) for w in re.findall(r"[A-Za-zÀ-ü]{3,}", name)) if t not in _GRANT_STOPWORDS)


def _build_grants(metadata: list[dict]) -> dict[str, dict]:
    grants: dict[str, dict] = {}
    for i, meta in enumerate(metadata):
        if meta.get("chunk_index", 0) != 0:
            continue
        head = meta.get("text", "")[:1500]
        m = _GRANT_RE.search(head)
        if not m:
            continue
        name = re.sub(r"\s+", " ", m[1]).strip()
        amount = None
        a = _GRANT_AMOUNT_RE.search(m[2])
        if a:
            try:
                amount = float(a[0].replace(".", "").replace(",", "."))
            except ValueError:
                amount = None
        d = _GRANT_DATE_RE.search(head)
        when = f"{d[3]}-{d[2]}-{d[1]}" if d else ""
        entry = grants.setdefault(name, {"tokens": _grant_tokens(name), "rows": []})
        entry["rows"].append((when, amount, i))
    df: dict[str, int] = {}
    for entry in grants.values():
        for t in entry["tokens"]:
            df[t] = df.get(t, 0) + 1
    for entry in grants.values():
        entry["rare"] = frozenset(t for t in entry["tokens"] if df[t] <= _GRANT_RARE_WORD)
    return grants


def find_grants(words: set[str], year: int | None = None, limit: int = 4) -> tuple[list[dict], dict]:
    """The grant cards of the beneficiaries whose distinctive name words are all in `words` (or at
    least two of them, or one of five letters or more that few beneficiaries have), the `limit` most recent (of `year`, when
    given), and a summary: names, number of cards, their years, the sum of the amounts. ([], {}) when
    no beneficiary matches, or too many."""
    global _grants
    plain = {_plain(w) for w in words}
    with _lock:
        _ensure_loaded()
        if _grants is None:
            _grants = _build_grants(_metadata)
            log.info(f"Grants by beneficiary: {len(_grants)} beneficiaries")
        matched = []
        for name, entry in _grants.items():
            tokens = entry["tokens"]
            if not tokens:
                continue
            found = tokens & plain
            if (len(found) == len(tokens) or len(found) >= 2
                    or any(len(t) >= _GRANT_RARE_WORD_LEN for t in found & entry["rare"])):
                matched.append(name)
        if not matched or len(matched) > _GRANT_MAX_BENEFICIARIES:
            return [], {}
        rows = [r for name in matched for r in _grants[name]["rows"]]
        if year is not None:
            of_year = [r for r in rows if r[0].startswith(str(year))]
            rows = of_year or rows
            year = year if of_year else None
        rows.sort(key=lambda r: r[0], reverse=True)
        amounts = [r[1] for r in rows if r[1] is not None]
        years = sorted({r[0][:4] for r in rows if r[0]})
        info = {"beneficiaries": sorted(matched), "count": len(rows), "year": year,
                "years": (years[0], years[-1]) if years else ("", ""),
                "total": round(sum(amounts), 2), "with_amount": len(amounts)}
        return [_result(idx, 1.0) for _, _, idx in rows[:limit]], info


def document_passages(chosen: list[dict], query_embedding: list[float], limit: int = 2) -> list[dict]:
    """When the search picked the first passage of a document in several passages (the one that names
    it and lists its attachments), the passage of the same document closest to the question: the
    figure asked for is in the attachment, not in the heading. The `limit` best overall."""
    with _lock:
        _ensure_loaded()
        if _embeddings is None or not _ids or limit <= 0:
            return []
        q = np.array(query_embedding, dtype=np.float32)
        norm = np.linalg.norm(q)
        if norm == 0:
            return []
        q = q / norm
        rows = _rows_by_source()
        best: list[tuple[float, int]] = []
        seen: set[str] = set()
        for c in chosen:
            src = c.get("source", "")
            r = rows.get(src, [])
            if src in seen or len(r) < 2 or _metadata[r[0]].get("text", "") != c.get("text", ""):
                continue
            seen.add(src)
            scores = _embeddings[r[1:]] @ q
            j = int(np.argmax(scores))
            best.append((float(scores[j]), r[1 + j]))
        best.sort(key=lambda b: b[0], reverse=True)
        out = []
        for score, idx in best[:limit]:
            meta = _metadata[idx]
            doc = _kept_meta(meta)
            out.append({
                "text":     meta.get("text", ""),
                "source":   doc.get("source", ""),
                "title":    doc.get("title", ""),
                "display_title": _title(doc),
                "doc_type": meta.get("doc_type", ""),
                "category": meta.get("category", ""),
                "date":     meta.get("date", ""),
                "score":    score,
            })
        return out


def get_indexed_sources() -> set[str]:
    """Returns the set of URLs (source) already present in the vector store."""
    _ensure_loaded()
    return {m.get("source", "") for m in _metadata if m.get("source")}


def all_chunks_exist(chunk_ids: list[str]) -> bool:
    """Returns True if all the IDs are already present in the vector store."""
    _ensure_loaded()
    return bool(chunk_ids) and all(cid in _id_to_idx for cid in chunk_ids)


def is_bm25_active() -> bool:
    _ensure_bm25()
    return _bm25 is not None


def get_top_doc() -> dict | None:
    """Returns the document with the most indexed chunks."""
    if not _metadata:
        return None
    counts = Counter(m.get("source", "") for m in _metadata if m.get("source"))
    if not counts:
        return None
    top_source, count = counts.most_common(1)[0]
    title = next((_title(m) for m in _metadata if m.get("source") == top_source), "")
    return {"source": top_source, "title": title, "chunks": count}


def get_stats() -> dict:
    _ensure_loaded()
    unique_sources = len({m.get("source", "") for m in _metadata if m.get("source")})
    _DOC_TYPE_NORM = {"html": "html", "pdf": "pdf", "document": "pdf"}
    doc_types: dict[str, int] = {}
    for m in _metadata:
        raw = m.get("doc_type", "html") or "html"
        t = _DOC_TYPE_NORM.get(raw, raw)
        doc_types[t] = doc_types.get(t, 0) + 1
    return {
        "collection": "numpy_store",
        "total_chunks": len(_ids),
        "unique_sources": unique_sources,
        "doc_types": doc_types,
    }


def _apply_keep(keep: list[int]):
    """Reduces the store to the rows in `keep` (sorted indices) and realigns the indices."""
    global _embeddings, _metadata, _ids, _id_to_idx, _bm25_dirty, _pdf_title_count, _copy_of, _source_rows
    _embeddings = _embeddings[keep]
    _metadata   = [_metadata[i] for i in keep]
    _ids        = [_ids[i] for i in keep]
    _id_to_idx  = {id_: i for i, id_ in enumerate(_ids)}
    _bm25_dirty = True
    _copy_of = None
    _source_rows = None
    _pdf_title_count = None


def remove_sources(stale: set[str], keep_inbox: bool = True) -> int:
    """Removes all chunks whose sources are no longer in the current crawl.
    Inbox and news chunks stay (keep_inbox): they do not come from the crawl, so
    their absence from the site does not mean they are outdated.
    Returns the number of chunks removed."""
    if not stale:
        return 0
    _ensure_loaded()
    keep = [
        i for i, m in enumerate(_metadata)
        if m.get("source") not in stale or (keep_inbox and _is_protected(m))
    ]
    removed = len(_metadata) - len(keep)
    if removed == 0:
        return 0
    _apply_keep(keep)
    _save()
    log.info(f"Removed {removed} chunks from {len(stale)} stale sources.")
    return removed


def remove_chunk_ids(chunk_ids: set[str]) -> int:
    """Removes the chunks with the given ids (missing ones are ignored).
    Used to remove in bulk the chunks of a source that are no longer present
    in its updated version. Returns the number of chunks removed."""
    if not chunk_ids:
        return 0
    _ensure_loaded()
    drop = {_id_to_idx[c] for c in chunk_ids if c in _id_to_idx}
    if not drop:
        return 0
    keep = [i for i in range(len(_ids)) if i not in drop]
    _apply_keep(keep)
    _save()
    log.info(f"Removed {len(drop)} stale chunks.")
    return len(drop)


def clear_collection():
    global _load_error
    _load_error = None   # emptied on purpose: writing is allowed again
    _reset_state()
    _save()
    log.info("Vector store emptied.")


def get_zero_vector_chunks() -> list[dict]:
    """Returns the chunks with a zero vector — checks the actual vector (not just the flag),
    so it also finds chunks saved before the needs_reembedding flag was introduced."""
    _ensure_loaded()
    if _embeddings is None or len(_ids) == 0:
        return []
    norms = np.linalg.norm(_embeddings, axis=1)
    return [
        {"idx": i, "id": _ids[i], "text": _metadata[i].get("text", ""),
         "source": _metadata[i].get("source", "")}
        for i, norm in enumerate(norms)
        if norm == 0
    ]


def update_embeddings(updates: list[tuple[int, list[float]]]) -> int:
    """Updates embeddings in place for a list of (idx, vector).
    Removes needs_reembedding if the vector is valid. Returns the number updated."""
    _ensure_loaded()
    updated = 0
    for idx, vec in updates:
        norm_vec = np.array(vec, dtype=np.float32)
        n = np.linalg.norm(norm_vec)
        if n > 0:
            _embeddings[idx] = norm_vec / n
            _metadata[idx].pop("needs_reembedding", None)
            updated += 1
    if updated:
        _save()
    return updated
