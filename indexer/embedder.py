"""
Generazione embedding tramite Ollama (modello OLLAMA_EMBED_MODEL, in
produzione mxbai-embed-large: contesto massimo 512 token).

Strategia: tenta prima il batch endpoint /api/embed (veloce quando funziona).
Se restituisce 400, cade back su chiamate singole su /api/embed. Su errore 500
riprova con backoff esponenziale prima di restituire un vettore zero.

Troncamento: Ollama 0.19 IGNORA `truncate: true` per mxbai-embed-large e
risponde 400 "the input length exceeds the context length" (512 token) sia in
batch sia in singolo; l'endpoint legacy /api/embeddings risponde 500 per lo
stesso motivo. Ne restavano 5.861 chunk a vettore zero al 16/09/2026 (testi
di 500-1500 caratteri ma densi di token: tabelle numeriche, PDF illeggibili).
Il troncamento va quindi fatto lato client: su 400 "context length" il testo
viene ritagliato a _CUT_STEPS caratteri in sequenza finché Ollama lo accetta
(verificato sul server: 800 caratteri passano su tutti i campioni).
"""

import logging
import random
import re
import time
import httpx
from concurrent.futures import ThreadPoolExecutor, as_completed

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import OLLAMA_BASE_URL, OLLAMA_EMBED_MODEL, EMBEDDING_DIMENSION

log = logging.getLogger(__name__)

BATCH_SIZE = 16          # batch più piccoli → meno tempo sprecato su 400
CONCURRENCY = 1          # Ollama serializza comunque le richieste di embedding
_MAX_CHARS  = 6000       # limite di sicurezza lato client (i chunk sono < 1500 ch)
_CUT_STEPS  = (800, 500, 300)  # tagli progressivi quando Ollama rifiuta il testo
                               # per il contesto del modello (512 token per mxbai)
_CTX_ERROR  = "context length"
MAX_RETRIES = 3          # tentativi totali prima di arrendersi con vettore zero
BATCH_GIVE_UP = 3        # batch 400 consecutivi dopo i quali si usano solo singoli
RETRY_BASE_DELAY = 1.0   # backoff: 1s, 2s, 4s (+ jitter)


def _zero() -> list[float]:
    return [0.0] * EMBEDDING_DIMENSION


_CTRL_CHARS = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')


def _clean(text: str) -> str:
    """Rimuove caratteri di controllo ASCII che causano 400 su /api/embed."""
    text = _CTRL_CHARS.sub(' ', text)
    return text[:_MAX_CHARS] if len(text) > _MAX_CHARS else text


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Genera embedding per una lista di testi tramite Ollama."""
    if not texts:
        return []

    all_embeddings = []
    total = len(texts)
    batch_failures = 0   # 400 consecutivi del batch endpoint

    with httpx.Client(timeout=120.0) as client:
        for i in range(0, total, BATCH_SIZE):
            batch = texts[i : i + BATCH_SIZE]
            batch_num = i // BATCH_SIZE + 1
            total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE
            log.info(f"  Embedding {batch_num}/{total_batches} ({len(batch)} testi)...")
            if batch_failures >= BATCH_GIVE_UP:
                # Un batch rifiutato costa 30-50 s: se falliscono in serie (es.
                # reembed dei chunk problematici) si passa ai singoli.
                vecs = _embed_parallel(client, batch)
            else:
                vecs = _try_batch(client, batch)
                if vecs is None:
                    batch_failures += 1
                    if batch_failures == BATCH_GIVE_UP:
                        log.warning(f"  {BATCH_GIVE_UP} batch consecutivi rifiutati: "
                                    "singoli per il resto della corsa")
                    else:
                        log.warning(f"  Batch {len(batch)} testi fallito, uso singoli paralleli...")
                    vecs = _embed_parallel(client, batch)
                else:
                    batch_failures = 0
            all_embeddings.extend(vecs)

    return all_embeddings


def embed_query(query: str) -> list[float]:
    """Genera embedding per una singola query utente."""
    with httpx.Client(timeout=30.0) as client:
        result = _try_batch(client, [query])
        if result:
            return result[0]
        return _embed_one(client, query)


def _try_batch(client: httpx.Client, texts: list[str]) -> list[list[float]] | None:
    """Tenta /api/embed con retry su 500. Ritorna None su 400 (fallback a singoli)."""
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = client.post(
                f"{OLLAMA_BASE_URL}/api/embed",
                json={"model": OLLAMA_EMBED_MODEL, "input": [_clean(t) for t in texts]},
            )
            if resp.status_code == 200:
                return resp.json()["embeddings"]
            if resp.status_code == 400:
                # Input non valido: fallback a singoli senza retry. Il corpo
                # della risposta dice quale controllo di Ollama è scattato.
                log.warning(f"  Batch Ollama 400: {resp.text[:200]!r}")
                return None
            if attempt == MAX_RETRIES:
                log.warning(f"  Batch Ollama HTTP {resp.status_code} dopo {MAX_RETRIES} retry")
                return None
        except Exception as e:
            if attempt == MAX_RETRIES:
                log.warning(f"  Batch Ollama errore dopo {MAX_RETRIES} retry: {e}")
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
    """Singola chiamata a /api/embed con retry su 500. Se Ollama rifiuta il
    testo perché supera il contesto del modello (400, il flag `truncate` non
    basta) ritenta con tagli progressivi lato client (_CUT_STEPS). Per gli
    altri 400 prova l'endpoint legacy /api/embeddings. Vettore zero solo se
    tutto fallisce."""
    cleaned = _clean(text)
    lengths = [len(cleaned)] + [n for n in _CUT_STEPS if n < len(cleaned)]
    for n in lengths:
        vec, too_long = _embed_request(client, cleaned[:n], text)
        if vec is not None:
            if n < len(cleaned):
                log.info(f"  Testo troncato a {n} caratteri per il contesto del modello: {text[:60]!r}")
            return vec
        if not too_long:
            return _zero()
    log.warning(f"  Testo oltre il contesto anche a {lengths[-1]} caratteri, vettore zero: {text[:80]!r}")
    return _zero()


def _embed_request(client: httpx.Client, text: str,
                   original: str) -> tuple[list[float] | None, bool]:
    """Una richiesta a /api/embed con retry su 500. Ritorna (vettore, False) su
    successo, (None, True) se il testo supera il contesto del modello, (vettore
    legacy o zero, False) per gli altri 400, (None, False) se tutto fallisce."""
    payload = {"model": OLLAMA_EMBED_MODEL, "input": [text], "truncate": True}
    for attempt in range(MAX_RETRIES + 1):
        status = "errore"
        try:
            resp = client.post(f"{OLLAMA_BASE_URL}/api/embed", json=payload)
            status = resp.status_code
            if resp.status_code == 200:
                embeddings = resp.json().get("embeddings") or []
                if embeddings:
                    return embeddings[0], False
                log.warning("  /api/embed ha risposto senza embedding, vettore zero")
                return None, False
            if resp.status_code == 400:
                if _CTX_ERROR in resp.text:
                    return None, True
                log.warning(f"  /api/embed 400: {resp.text[:200]!r} — testo: {original[:80]!r}")
                return _embed_one_legacy(client, text), False
            if attempt == MAX_RETRIES:
                log.warning(f"  Embedding singolo fallito (HTTP {resp.status_code}), vettore zero")
                return None, False
        except Exception as e:
            if attempt == MAX_RETRIES:
                log.warning(f"  Embedding singolo fallito: {e}, vettore zero")
                return None, False
        delay = RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.5)
        log.warning(f"  Ollama {status}, retry {attempt + 1}/{MAX_RETRIES} tra {delay:.1f}s...")
        time.sleep(delay)
    return None, False


def _embed_one_legacy(client: httpx.Client, text: str) -> list[float]:
    """Ultimo tentativo su /api/embeddings per i 400 non dovuti al contesto.
    Non tronca: oltre il contesto del modello risponde 500 (vettore zero)."""
    try:
        resp = client.post(
            f"{OLLAMA_BASE_URL}/api/embeddings",
            json={"model": OLLAMA_EMBED_MODEL, "prompt": _clean(text)},
        )
        if resp.status_code == 200:
            return resp.json()["embedding"]
        log.warning(f"  /api/embeddings HTTP {resp.status_code}: {resp.text[:200]!r}, vettore zero")
    except Exception as e:
        log.warning(f"  /api/embeddings errore: {e}, vettore zero")
    return _zero()


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
