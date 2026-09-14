"""
Vector store basato su numpy — nessuna dipendenza esterna da compilare.
Salva embedding e metadata su disco, esegue ricerca coseno in memoria.

Supporta hybrid search: cosine similarity (vettori) + BM25 (keyword),
combinati tramite Reciprocal Rank Fusion (RRF).

Salvataggio su disco: ogni modifica (upsert/rimozione) riscrive i tre file
dello store (embeddings.npy, metadata.json, ids.json — oltre 1 GB con 240k
chunk). Durante un'indicizzazione lunga usare `deferred_saves()`: le
scritture vengono rinviate ai `checkpoint()` periodici e all'uscita dal
blocco, invece di una riscrittura completa per ogni documento.
"""

import hashlib
import json
import logging
import re
import time
from contextlib import contextmanager
from pathlib import Path

import numpy as np

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import VECTOR_STORE_DIR, RETRIEVAL_TOP_K

log = logging.getLogger(__name__)

EMBEDDINGS_FILE = VECTOR_STORE_DIR / "embeddings.npy"
METADATA_FILE   = VECTOR_STORE_DIR / "metadata.json"
IDS_FILE        = VECTOR_STORE_DIR / "ids.json"

CHECKPOINT_INTERVAL = 1800  # secondi tra due salvataggi in modalità deferred

# Stato in memoria (caricato una volta sola)
_embeddings: np.ndarray | None = None   # shape (N, D)
_metadata: list[dict] = []
_ids: list[str] = []
_id_to_idx: dict[str, int] = {}
_bm25 = None          # indice BM25, ricostruito (lazy) quando il corpus cambia
_bm25_dirty = False   # True se il corpus è cambiato dopo l'ultima costruzione

# Salvataggio differito (vedi deferred_saves / checkpoint)
_deferred_depth = 0
_dirty = False        # modifiche in memoria non ancora scritte su disco
_last_save_ts = 0.0


def _ensure_loaded():
    global _embeddings, _metadata, _ids, _id_to_idx, _last_save_ts
    if _embeddings is not None:
        return
    VECTOR_STORE_DIR.mkdir(parents=True, exist_ok=True)
    if EMBEDDINGS_FILE.exists() and METADATA_FILE.exists() and IDS_FILE.exists():
        try:
            _embeddings = np.load(str(EMBEDDINGS_FILE))
            with open(METADATA_FILE, encoding="utf-8") as f:
                _metadata = json.load(f)
            with open(IDS_FILE, encoding="utf-8") as f:
                _ids = json.load(f)
            _id_to_idx = {id_: i for i, id_ in enumerate(_ids)}
            dim = _embeddings.shape[1] if _embeddings.ndim == 2 else 0
            log.info(f"Vector store caricato: {len(_ids)} chunk, dim={dim}")
            _build_bm25()
        except Exception as e:
            log.warning(f"Errore caricamento vector store: {e} — parto da zero")
            _reset_state()
    else:
        _reset_state()
    _last_save_ts = time.time()


def _reset_state():
    global _embeddings, _metadata, _ids, _id_to_idx, _bm25, _bm25_dirty
    _embeddings = None  # dimensione determinata dal primo batch
    _metadata = []
    _ids = []
    _id_to_idx = {}
    _bm25 = None
    _bm25_dirty = False


def _tokenize(text: str) -> list[str]:
    """Tokenizzazione semplice per BM25: lowercase, solo parole."""
    return re.findall(r"[a-zàáâäèéêëìíîïòóôöùúûü]+", text.lower())


def _build_bm25():
    """Costruisce o ricostruisce l'indice BM25 dal corpus corrente."""
    global _bm25, _bm25_dirty
    _bm25_dirty = False
    if not _metadata:
        _bm25 = None
        return
    try:
        from rank_bm25 import BM25Okapi
        corpus = [_tokenize(m.get("text", "")) for m in _metadata]
        _bm25 = BM25Okapi(corpus)
    except ImportError:
        log.warning("rank_bm25 non installato — hybrid search disabilitato. "
                    "Installa con: pip install rank-bm25")
        _bm25 = None


def _ensure_bm25():
    """Ricostruisce BM25 solo se il corpus è cambiato dall'ultima costruzione.
    La ricostruzione (tokenizzazione di tutto il corpus) è costosa: va fatta
    una volta prima di una ricerca, non a ogni salvataggio."""
    if _bm25_dirty:
        _build_bm25()


def _atomic_write_json(path: Path, data):
    """Scrive JSON in modo atomico: prima su .tmp, poi rinomina."""
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    tmp.replace(path)  # atomico su tutti i filesystem POSIX


def _atomic_write_npy(path: Path, arr: np.ndarray):
    """Scrive l'array in modo atomico. np.save su un file aperto non aggiunge
    l'estensione .npy, quindi il nome temporaneo resta quello scelto qui."""
    tmp = path.with_suffix(".tmp")
    with open(tmp, "wb") as f:
        np.save(f, arr)
    tmp.replace(path)


def _write_to_disk():
    global _dirty, _last_save_ts
    VECTOR_STORE_DIR.mkdir(parents=True, exist_ok=True)
    arr = _embeddings if _embeddings is not None else np.zeros((0, 0), dtype=np.float32)
    _atomic_write_npy(EMBEDDINGS_FILE, arr)
    _atomic_write_json(METADATA_FILE, _metadata)
    _atomic_write_json(IDS_FILE, _ids)
    _dirty = False
    _last_save_ts = time.time()


def _save():
    """Persiste lo stato. Dentro `deferred_saves()` segna solo lo store come
    modificato: la scrittura avviene al checkpoint o all'uscita dal blocco."""
    global _dirty
    if _deferred_depth > 0:
        _dirty = True
        return
    _write_to_disk()


@contextmanager
def deferred_saves():
    """Rinvia le scritture su disco per tutta la durata del blocco.
    All'uscita (anche per eccezione) scrive se ci sono modifiche pendenti."""
    global _deferred_depth
    _deferred_depth += 1
    try:
        yield
    finally:
        _deferred_depth -= 1
        if _deferred_depth == 0 and _dirty:
            _write_to_disk()


def checkpoint_due() -> bool:
    """True se è passato CHECKPOINT_INTERVAL dall'ultimo salvataggio."""
    return time.time() - _last_save_ts >= CHECKPOINT_INTERVAL


def checkpoint(force: bool = False) -> bool:
    """Scrive su disco le modifiche pendenti se `force` o se è scaduto
    l'intervallo. Ritorna True se ha scritto."""
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
    Inserisce o aggiorna chunk nel vector store.
    chunks: lista di {"text": str, "metadata": dict}
    embeddings: un vettore per chunk. `None` significa "chunk già presente
    con lo stesso testo": aggiorna solo i metadati, il vettore resta quello
    in memoria (evita di richiamare Ollama per contenuti invariati).
    Ritorna il numero di chunk inseriti o aggiornati con un nuovo vettore.
    """
    global _embeddings, _metadata, _ids, _id_to_idx, _bm25_dirty

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
        # Sanifica i metadati: bytes non è serializzabile in JSON
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
                log.warning(f"  Chunk {cid} senza embedding e non presente nello store, ignorato")
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
            # Aggiorna in place
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
        _save()
    return inserted + updated


def chunks_to_embed(chunks: list[dict], ids: list[str]) -> list[bool]:
    """Per ogni chunk dice se serve (ri)calcolare l'embedding: id assente
    nello store, testo diverso da quello memorizzato, oppure vettore zero
    (embedding fallito in un sync precedente)."""
    _ensure_loaded()
    flags = []
    for chunk, cid in zip(chunks, ids):
        idx = _id_to_idx.get(cid)
        if idx is None:
            flags.append(True)
        elif _metadata[idx].get("text") != chunk["text"]:
            flags.append(True)
        elif not np.any(_embeddings[idx]):
            flags.append(True)
        else:
            flags.append(False)
    return flags


def _is_inbox(meta: dict) -> bool:
    """Chunk caricato a mano dall'inbox (scripts/inbox_indexer): va conservato
    anche se la sua sorgente non compare nel crawl o viene ricrawlata.
    `doc_type == "document"` copre i documenti inbox precedenti al marker."""
    return meta.get("origin") == "inbox" or meta.get("doc_type") == "document"


def source_chunk_ids(source: str, include_inbox: bool = False) -> set[str]:
    """Ritorna gli id dei chunk attualmente nello store per una sorgente.
    I chunk inbox sono esclusi di default: l'indexer li userebbe come "stale"
    quando reindicizza la pagina crawlata con lo stesso URL."""
    _ensure_loaded()
    return {
        _ids[i] for i, m in enumerate(_metadata)
        if m.get("source") == source and (include_inbox or not _is_inbox(m))
    }


def inbox_sources() -> set[str]:
    """Sorgenti dei chunk caricati dall'inbox."""
    _ensure_loaded()
    return {m.get("source", "") for m in _metadata if _is_inbox(m) and m.get("source")}


def search(query_embedding: list[float], top_k: int = RETRIEVAL_TOP_K) -> list[dict]:
    """
    Cerca i chunk più simili tramite cosine similarity.
    Ritorna lista di {"text", "source", "title", "score"}.
    """
    _ensure_loaded()

    if _embeddings is None or len(_ids) == 0:
        log.warning("Vector store vuoto. Eseguire prima l'indicizzazione.")
        return []

    q = np.array(query_embedding, dtype=np.float32)
    n = np.linalg.norm(q)
    if n > 0:
        q = q / n

    # Cosine similarity = dot product (vettori già normalizzati)
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
    Ricerca ibrida: combina cosine similarity e BM25 tramite weighted RRF.

    vector_weight + bm25_weight = 1.0 (raccomandato)
    """
    _ensure_loaded()

    if _embeddings is None or len(_ids) == 0:
        log.warning("Vector store vuoto.")
        return []

    _ensure_bm25()

    n_docs = len(_ids)
    fetch_k = min(top_k * 20, n_docs)  # recupera più candidati per la fusione

    # --- Ricerca vettoriale ---
    q = np.array(query_embedding, dtype=np.float32)
    norm = np.linalg.norm(q)
    if norm > 0:
        q = q / norm
    vec_scores = _embeddings @ q

    vec_top = np.argpartition(vec_scores, -fetch_k)[-fetch_k:]
    vec_top = vec_top[np.argsort(vec_scores[vec_top])[::-1]]

    # Rank vettoriale: posizione → score RRF
    vec_rrf = {}
    for rank, idx in enumerate(vec_top):
        vec_rrf[int(idx)] = 1.0 / (60 + rank + 1)

    # --- Ricerca BM25 ---
    bm25_rrf = {}
    if _bm25 is not None:
        tokens = _tokenize(query_text)
        if tokens:
            bm25_scores = _bm25.get_scores(tokens)
            bm25_top = np.argpartition(bm25_scores, -fetch_k)[-fetch_k:]
            bm25_top = bm25_top[np.argsort(bm25_scores[bm25_top])[::-1]]
            for rank, idx in enumerate(bm25_top):
                bm25_rrf[int(idx)] = 1.0 / (60 + rank + 1)

    # --- Fusione pesi ---
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
            "doc_type": meta.get("doc_type", ""),
            "category": meta.get("category", ""),
            "score":    float(vec_scores[idx]),  # score semantico per il filtro MIN_SIMILARITY
        })

    return results


def get_indexed_sources() -> set[str]:
    """Ritorna il set di URL (source) già presenti nel vector store."""
    _ensure_loaded()
    return {m.get("source", "") for m in _metadata if m.get("source")}


def all_chunks_exist(chunk_ids: list[str]) -> bool:
    """Ritorna True se tutti gli ID sono già presenti nel vector store."""
    _ensure_loaded()
    return bool(chunk_ids) and all(cid in _id_to_idx for cid in chunk_ids)


def is_bm25_active() -> bool:
    _ensure_bm25()
    return _bm25 is not None


def get_top_doc() -> dict | None:
    """Ritorna il documento con più chunk indicizzati."""
    if not _metadata:
        return None
    from collections import Counter
    counts = Counter(m.get("source", "") for m in _metadata if m.get("source"))
    if not counts:
        return None
    top_source, count = counts.most_common(1)[0]
    title = next((m.get("title", "") for m in _metadata if m.get("source") == top_source), "")
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
    """Riduce lo store alle righe in `keep` (indici ordinati) e riallinea gli indici."""
    global _embeddings, _metadata, _ids, _id_to_idx, _bm25_dirty
    _embeddings = _embeddings[keep]
    _metadata   = [_metadata[i] for i in keep]
    _ids        = [_ids[i] for i in keep]
    _id_to_idx  = {id_: i for i, id_ in enumerate(_ids)}
    _bm25_dirty = True


def remove_sources(stale: set[str], keep_inbox: bool = True) -> int:
    """Rimuove tutti i chunk le cui sorgenti non sono più nel crawl corrente.
    I chunk inbox restano (keep_inbox): non provengono dal crawl, quindi la
    loro assenza dal sito non significa che siano superati.
    Ritorna il numero di chunk rimossi."""
    if not stale:
        return 0
    _ensure_loaded()
    keep = [
        i for i, m in enumerate(_metadata)
        if m.get("source") not in stale or (keep_inbox and _is_inbox(m))
    ]
    removed = len(_metadata) - len(keep)
    if removed == 0:
        return 0
    _apply_keep(keep)
    _save()
    log.info(f"Rimossi {removed} chunk da {len(stale)} sorgenti stale.")
    return removed


def remove_chunk_ids(chunk_ids: set[str]) -> int:
    """Rimuove i chunk con gli id indicati (quelli assenti sono ignorati).
    Usato per eliminare in blocco i chunk di una sorgente non più presenti
    nella sua versione aggiornata. Ritorna il numero di chunk rimossi."""
    if not chunk_ids:
        return 0
    _ensure_loaded()
    drop = {_id_to_idx[c] for c in chunk_ids if c in _id_to_idx}
    if not drop:
        return 0
    keep = [i for i in range(len(_ids)) if i not in drop]
    _apply_keep(keep)
    _save()
    log.info(f"Rimossi {len(drop)} chunk stale.")
    return len(drop)


def clear_collection():
    _reset_state()
    _save()
    log.info("Vector store svuotato.")


def get_zero_vector_chunks() -> list[dict]:
    """Ritorna i chunk con vettore zero — controlla il vettore effettivo (non solo il flag),
    così trova anche chunk salvati prima dell'introduzione del flag needs_reembedding."""
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
    """Aggiorna embedding in place per una lista di (idx, vector).
    Rimuove needs_reembedding se il vettore è valido. Ritorna il numero aggiornati."""
    global _embeddings
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
