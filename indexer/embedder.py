"""
Generazione embedding tramite Ollama (nomic-embed-text).

Strategia: tenta prima il batch endpoint /api/embed (veloce quando funziona).
Se restituisce 400, cade back su chiamate singole concorrenti con ThreadPoolExecutor
(più veloce del fallback sequenziale su macchine con Ollama lento).
Su errore 500 (Ollama sovraccarico) riprova con backoff esponenziale prima di
restituire un vettore zero.
"""

import logging
import random
import time
import httpx
from concurrent.futures import ThreadPoolExecutor, as_completed

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import OLLAMA_BASE_URL, OLLAMA_EMBED_MODEL, EMBEDDING_DIMENSION

log = logging.getLogger(__name__)

BATCH_SIZE = 16          # batch più piccoli → meno tempo sprecato su 400
CONCURRENCY = 1          # Ollama non gestisce concorrenza su /api/embeddings
_MAX_CHARS  = 6000       # nomic-embed-text: 8192 token max, ~6000 char safe
MAX_RETRIES = 3          # tentativi totali prima di arrendersi con vettore zero
RETRY_BASE_DELAY = 1.0   # backoff: 1s, 2s, 4s (+ jitter)


def _truncate(text: str) -> str:
    return text[:_MAX_CHARS] if len(text) > _MAX_CHARS else text


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Genera embedding per una lista di testi tramite Ollama."""
    if not texts:
        return []

    all_embeddings = []
    total = len(texts)

    with httpx.Client(timeout=120.0) as client:
        for i in range(0, total, BATCH_SIZE):
            batch = texts[i : i + BATCH_SIZE]
            batch_num = i // BATCH_SIZE + 1
            total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE
            log.info(f"  Embedding {batch_num}/{total_batches} ({len(batch)} testi)...")
            vecs = _embed_batch(client, batch)
            all_embeddings.extend(vecs)

    return all_embeddings


def embed_query(query: str) -> list[float]:
    """Genera embedding per una singola query utente."""
    with httpx.Client(timeout=30.0) as client:
        result = _try_batch(client, [query])
        if result:
            return result[0]
        return _embed_one(client, query)


def _embed_batch(client: httpx.Client, texts: list[str]) -> list[list[float]]:
    """Prova il batch endpoint; se fallisce usa chiamate singole parallele."""
    result = _try_batch(client, texts)
    if result is not None:
        return result

    log.warning(f"  Batch {len(texts)} testi fallito, uso singoli paralleli...")
    return _embed_parallel(client, texts)


def _try_batch(client: httpx.Client, texts: list[str]) -> list[list[float]] | None:
    """Tenta /api/embed con retry su 500. Ritorna None su 400 (fallback a singoli)."""
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = client.post(
                f"{OLLAMA_BASE_URL}/api/embed",
                json={"model": OLLAMA_EMBED_MODEL, "input": [_truncate(t) for t in texts]},
            )
            if resp.status_code == 200:
                return resp.json()["embeddings"]
            if resp.status_code == 400:
                return None  # input non valido, fallback senza retry
            if attempt == MAX_RETRIES:
                return None
        except Exception:
            if attempt == MAX_RETRIES:
                return None
        delay = RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.5)
        log.warning(f"  Batch Ollama 500, retry {attempt + 1}/{MAX_RETRIES} tra {delay:.1f}s...")
        time.sleep(delay)
    return None


def _embed_parallel(client: httpx.Client, texts: list[str]) -> list[list[float]]:
    """Chiama /api/embeddings in parallelo con CONCURRENCY thread."""
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
    """Singola chiamata a /api/embeddings con retry su 500. Vettore zero solo se tutti i retry falliscono."""
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = client.post(
                f"{OLLAMA_BASE_URL}/api/embeddings",
                json={"model": OLLAMA_EMBED_MODEL, "prompt": _truncate(text)},
            )
            if resp.status_code == 200:
                return resp.json()["embedding"]
            if resp.status_code != 500 or attempt == MAX_RETRIES:
                log.warning(f"  Embedding singolo fallito (HTTP {resp.status_code}), vettore zero")
                return [0.0] * EMBEDDING_DIMENSION
        except Exception as e:
            if attempt == MAX_RETRIES:
                log.warning(f"  Embedding singolo fallito: {e}, vettore zero")
                return [0.0] * EMBEDDING_DIMENSION
        delay = RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.5)
        log.warning(f"  Ollama 500, retry {attempt + 1}/{MAX_RETRIES} tra {delay:.1f}s...")
        time.sleep(delay)
    return [0.0] * EMBEDDING_DIMENSION


def check_ollama_embed() -> bool:
    """Verifica che Ollama sia raggiungibile e il modello di embedding disponibile."""
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"{OLLAMA_BASE_URL}/api/embed",
                json={"model": OLLAMA_EMBED_MODEL, "input": ["test"]},
            )
            return resp.status_code == 200
    except Exception:
        return False
