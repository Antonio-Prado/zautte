"""
Generazione embedding tramite Ollama (nomic-embed-text).

Strategia: tenta prima il batch endpoint /api/embed (veloce quando funziona).
Se restituisce 400, cade back su chiamate singole concorrenti con ThreadPoolExecutor
(più veloce del fallback sequenziale su macchine con Ollama lento).
"""

import logging
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
    """Tenta /api/embed. Ritorna None se fallisce."""
    try:
        resp = client.post(
            f"{OLLAMA_BASE_URL}/api/embed",
            json={"model": OLLAMA_EMBED_MODEL, "input": [_truncate(t) for t in texts]},
        )
        if resp.status_code == 200:
            return resp.json()["embeddings"]
        return None
    except Exception:
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
    """Singola chiamata a /api/embeddings. Ritorna vettore zero su errore."""
    try:
        resp = client.post(
            f"{OLLAMA_BASE_URL}/api/embeddings",
            json={"model": OLLAMA_EMBED_MODEL, "prompt": _truncate(text)},
        )
        resp.raise_for_status()
        return resp.json()["embedding"]
    except Exception as e:
        log.warning(f"  Embedding singolo fallito, vettore zero: {e}")
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
