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
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import CRAWL_CACHE_DIR, RETRIEVAL_TOP_K, VECTOR_STORE_DIR
from indexer.titles import LINK_TEXTS_FILE, display_title, is_weak, title_from_text, with_text_title

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
_pdf_title_count: Counter | None = None  # /Title → number of PDFs using it (lazy)
_pdf_first_text: dict[str, str] = {}      # PDF source → text of the first chunk
_pdf_display: dict[str, str] = {}         # PDF source → title to display (cache)
_pdf_link_texts: dict[str, str] = {}      # PDF source → link text (from the crawler)

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
    _check_manifest()
    embeddings = np.load(str(EMBEDDINGS_FILE), allow_pickle=False)
    with open(METADATA_FILE, encoding="utf-8") as f:
        metadata = json.load(f)
    with open(IDS_FILE, encoding="utf-8") as f:
        ids = json.load(f)
    if not (len(metadata) == len(ids) == embeddings.shape[0]):
        raise ValueError(f"store incoerente: {embeddings.shape[0]} vettori, "
                         f"{len(metadata)} metadati, {len(ids)} id")
    return _sanitize(embeddings), metadata, ids


def _ensure_loaded():
    global _embeddings, _metadata, _ids, _id_to_idx, _last_save_ts, _load_error
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
    _last_save_ts = time.time()


def reload() -> dict:
    """Reloads the store from disk while the process keeps answering:
    lets the API see the passages written by `scripts.sync news` without the
    restart (20-30 s of 502) that follows the weekly syncs. The new store and
    its BM25 are built separately and swapped under `_lock`, so no
    search sees a half-built state. If the files are unreadable the old one
    stays. Returns the stats with `reloaded` True/False."""
    global _embeddings, _metadata, _ids, _id_to_idx, _bm25, _bm25_dirty
    global _pdf_title_count, _pdf_first_text, _pdf_display, _pdf_link_texts, _last_save_ts
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
    with _lock:
        _embeddings, _metadata, _ids = embeddings, metadata, ids
        _id_to_idx = {id_: i for i, id_ in enumerate(_ids)}
        _bm25, _bm25_dirty = bm25, False
        _pdf_title_count, _pdf_first_text, _pdf_display, _pdf_link_texts = None, {}, {}, {}
        _last_save_ts = time.time()
    log.info(f"Vector store reloaded: {len(_ids)} chunks")
    return {**get_stats(), "reloaded": True}


def _reset_state():
    global _embeddings, _metadata, _ids, _id_to_idx, _bm25, _bm25_dirty, _pdf_title_count
    _embeddings = None  # dimension determined by the first batch
    _metadata = []
    _ids = []
    _id_to_idx = {}
    _bm25 = None
    _bm25_dirty = False
    _pdf_title_count = None


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
    try:
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
    global _embeddings, _bm25_dirty, _pdf_title_count

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
    «stale»: inbox documents and Bollettino news indexed by
    `scripts.news_sync` (origin=news; their domain is not in the crawl).
    Unlike the inbox, news chunks remain replaceable by their own
    indexer when the text changes (see source_chunk_ids)."""
    return _is_inbox(meta) or meta.get("origin") == "news"


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


def _load_link_texts() -> dict[str, str]:
    """Link texts pointing to PDFs saved by the crawler (see crawler.save_link_texts)."""
    path = CRAWL_CACHE_DIR / LINK_TEXTS_FILE
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        log.warning(f"{path} unreadable: PDF titles without the link texts")
        return {}


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

    top_indices = sorted(fused, key=fused.__getitem__, reverse=True)[:top_k]

    results = []
    for idx in top_indices:
        meta = _metadata[idx]
        results.append({
            "text":     meta.get("text", ""),
            "source":   meta.get("source", ""),
            "title":    meta.get("title", ""),
            "display_title": _title(meta),
            "doc_type": meta.get("doc_type", ""),
            "category": meta.get("category", ""),
            "date":     meta.get("date", ""),   # shown to the model (build_context_block)
            "score":    float(vec_scores[idx]),  # semantic score for the MIN_SIMILARITY filter
        })

    return results


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
    global _embeddings, _metadata, _ids, _id_to_idx, _bm25_dirty, _pdf_title_count
    _embeddings = _embeddings[keep]
    _metadata   = [_metadata[i] for i in keep]
    _ids        = [_ids[i] for i in keep]
    _id_to_idx  = {id_: i for i, id_ in enumerate(_ids)}
    _bm25_dirty = True
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
