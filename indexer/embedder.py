"""
Embedding generation through Ollama (model OLLAMA_EMBED_MODEL, in
production bge-m3 since 04/10/2026: 8192-token context, passages never
exceed it; before that mxbai-embed-large, 512-token context).

Strategy: first try the batch endpoint /api/embed (fast when it works).
If it returns 400, fall back to single calls on /api/embed. On a 500 error
retry with exponential backoff before returning a zero vector.

Truncation: Ollama 0.19 IGNORES `truncate: true` for mxbai-embed-large and
answers 400 "the input length exceeds the context length" (512 tokens) both in
batch and single mode; the legacy /api/embeddings endpoint answers 500 for the
same reason. 5,861 chunks were left with a zero vector on 16/09/2026 (texts
of 500-1500 characters but token-dense: numeric tables, unreadable PDFs).
Truncation must therefore be done client-side: on a 400 "context length" the text
is cut to _CUT_STEPS characters in sequence until Ollama accepts it
(verified on the server: 800 characters pass on all samples). With bge-m3
it is not needed, but it stays for short-context models.
"""

import logging
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import EMBEDDING_DIMENSION, OLLAMA_BASE_URL, OLLAMA_EMBED_MODEL

log = logging.getLogger(__name__)

BATCH_SIZE = 16          # smaller batches → less time wasted on 400s
CONCURRENCY = 1          # Ollama serializes embedding requests anyway
_MAX_CHARS  = 6000       # client-side safety limit (chunks are < 1500 chars)
_CUT_STEPS  = (800, 500, 300)  # progressive cuts when Ollama rejects the text
                               # for the model context (512 tokens for mxbai)
_CTX_ERROR  = "context length"
MAX_RETRIES = 3          # total attempts before giving up with a zero vector
BATCH_GIVE_UP = 3        # consecutive batch 400s after which only single calls are used
RETRY_BASE_DELAY = 1.0   # backoff: 1s, 2s, 4s (+ jitter)
# Ollama options added to every request, e.g. {"num_thread": 12}. With all
# cores in use a long computation slows down tenfold as soon as another process takes
# one (a 3 s batch exceeded 120 s): scripts/reembed.py --threads leaves CPU
# free for the API. Empty = Ollama defaults.
OLLAMA_OPTIONS: dict = {}


def _body(**fields) -> dict:
    """Body of a request to Ollama: model, data fields and OLLAMA_OPTIONS."""
    body = {"model": OLLAMA_EMBED_MODEL, **fields}
    if OLLAMA_OPTIONS:
        body["options"] = OLLAMA_OPTIONS
    return body


def _zero() -> list[float]:
    return [0.0] * EMBEDDING_DIMENSION


_CTRL_CHARS = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')


def _clean(text: str) -> str:
    """Remove ASCII control characters that cause 400 on /api/embed."""
    text = _CTRL_CHARS.sub(' ', text)
    return text[:_MAX_CHARS] if len(text) > _MAX_CHARS else text


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Generate embeddings for a list of texts through Ollama."""
    if not texts:
        return []

    all_embeddings = []
    total = len(texts)
    batch_failures = 0   # consecutive 400s from the batch endpoint

    with httpx.Client(timeout=120.0) as client:
        for i in range(0, total, BATCH_SIZE):
            batch = texts[i : i + BATCH_SIZE]
            batch_num = i // BATCH_SIZE + 1
            total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE
            log.info(f"  Embedding {batch_num}/{total_batches} ({len(batch)} texts)...")
            if batch_failures >= BATCH_GIVE_UP:
                # A rejected batch costs 30-50 s: if they fail in a row (e.g.
                # re-embedding of problematic chunks) switch to single calls.
                vecs = _embed_parallel(client, batch)
            else:
                vecs = _try_batch(client, batch)
                if vecs is None:
                    batch_failures += 1
                    if batch_failures == BATCH_GIVE_UP:
                        log.warning(f"  {BATCH_GIVE_UP} consecutive batches rejected: "
                                    "single requests for the rest of the run")
                    else:
                        log.warning(f"  Batch of {len(batch)} texts failed, falling back to parallel single requests...")
                    vecs = _embed_parallel(client, batch)
                else:
                    batch_failures = 0
            all_embeddings.extend(vecs)

    return all_embeddings


def embed_query(query: str) -> list[float]:
    """Generate an embedding for a single user query."""
    with httpx.Client(timeout=30.0) as client:
        result = _try_batch(client, [query])
        if result:
            return result[0]
        return _embed_one(client, query)


def _try_batch(client: httpx.Client, texts: list[str]) -> list[list[float]] | None:
    """Try /api/embed with retry on 500. Return None on 400 (fall back to single calls)."""
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = client.post(
                f"{OLLAMA_BASE_URL}/api/embed",
                json=_body(input=[_clean(t) for t in texts]),
            )
            if resp.status_code == 200:
                return resp.json()["embeddings"]
            if resp.status_code == 400:
                # Invalid input: fall back to single calls without retry. The response
                # body says which Ollama check was triggered.
                log.warning(f"  Batch Ollama 400: {resp.text[:200]!r}")
                return None
            if attempt == MAX_RETRIES:
                log.warning(f"  Batch Ollama HTTP {resp.status_code} after {MAX_RETRIES} retries")
                return None
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
            if attempt == MAX_RETRIES:
                log.warning(f"  Batch Ollama error after {MAX_RETRIES} retries: {e}")
                return None
        delay = RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.5)
        log.warning(f"  Batch Ollama 500, retry {attempt + 1}/{MAX_RETRIES} in {delay:.1f}s...")
        time.sleep(delay)
    return None


def _embed_parallel(client: httpx.Client, texts: list[str]) -> list[list[float]]:
    """Call /api/embeddings in parallel with CONCURRENCY threads."""
    results = [None] * len(texts)

    def _work(idx: int, text: str):
        return idx, _embed_one(client, text)

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        futures = {pool.submit(_work, i, t): i for i, t in enumerate(texts)}
        for future in as_completed(futures):
            idx, vec = future.result()
            results[idx] = vec

    return results


def _embed_one(client: httpx.Client, text: str) -> list[float]:
    """Single call to /api/embed with retry on 500. If Ollama rejects the
    text because it exceeds the model context (400, the `truncate` flag is
    not enough) retry with progressive client-side cuts (_CUT_STEPS). For the
    other 400s try the legacy /api/embeddings endpoint. Zero vector only if
    everything fails."""
    cleaned = _clean(text)
    lengths = [len(cleaned)] + [n for n in _CUT_STEPS if n < len(cleaned)]
    for n in lengths:
        vec, too_long = _embed_request(client, cleaned[:n], text)
        if vec is not None:
            if n < len(cleaned):
                log.info(f"  Text truncated to {n} characters to fit the model context: {text[:60]!r}")
            return vec
        if not too_long:
            return _zero()
    log.warning(f"  Text exceeds the context even at {lengths[-1]} characters, zero vector: {text[:80]!r}")
    return _zero()


def _embed_request(client: httpx.Client, text: str,
                   original: str) -> tuple[list[float] | None, bool]:
    """One request to /api/embed with retry on 500. Return (vector, False) on
    success, (None, True) if the text exceeds the model context, (legacy
    or zero vector, False) for the other 400s, (None, False) if everything fails."""
    payload = _body(input=[text], truncate=True)
    for attempt in range(MAX_RETRIES + 1):
        status = "errore"
        try:
            resp = client.post(f"{OLLAMA_BASE_URL}/api/embed", json=payload)
            status = resp.status_code
            if resp.status_code == 200:
                embeddings = resp.json().get("embeddings") or []
                if embeddings:
                    return embeddings[0], False
                log.warning("  /api/embed returned no embedding, zero vector")
                return None, False
            if resp.status_code == 400:
                if _CTX_ERROR in resp.text:
                    return None, True
                log.warning(f"  /api/embed 400: {resp.text[:200]!r} — text: {original[:80]!r}")
                return _embed_one_legacy(client, text), False
            if attempt == MAX_RETRIES:
                log.warning(f"  Single embedding failed (HTTP {resp.status_code}), zero vector")
                return None, False
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
            if attempt == MAX_RETRIES:
                log.warning(f"  Single embedding failed: {e}, zero vector")
                return None, False
        delay = RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.5)
        log.warning(f"  Ollama {status}, retry {attempt + 1}/{MAX_RETRIES} in {delay:.1f}s...")
        time.sleep(delay)
    return None, False


def _embed_one_legacy(client: httpx.Client, text: str) -> list[float]:
    """Last attempt on /api/embeddings for 400s not caused by the context.
    It does not truncate: beyond the model context it answers 500 (zero vector)."""
    try:
        resp = client.post(
            f"{OLLAMA_BASE_URL}/api/embeddings",
            json=_body(prompt=_clean(text)),
        )
        if resp.status_code == 200:
            return resp.json()["embedding"]
        log.warning(f"  /api/embeddings HTTP {resp.status_code}: {resp.text[:200]!r}, zero vector")
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        log.warning(f"  /api/embeddings error: {e}, zero vector")
    return _zero()


def check_ollama_embed() -> bool:
    """Check that Ollama is reachable and the embedding model is available."""
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"{OLLAMA_BASE_URL}/api/embed",
                json=_body(input=["test"]),
            )
            return resp.status_code == 200
    except httpx.HTTPError:
        return False
