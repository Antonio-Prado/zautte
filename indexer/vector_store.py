"""
Vector store basato su numpy — nessuna dipendenza esterna da compilare.
Salva embedding e metadata su disco, esegue ricerca coseno in memoria.

Supporta hybrid search: cosine similarity (vettori) + BM25 (keyword),
combinati tramite Reciprocal Rank Fusion (RRF).

Per corpus di dimensione comunale (< 20.000 chunk) le prestazioni sono
più che adeguate: ricerca su 10.000 chunk in < 50ms.
"""

import hashlib
import json
import logging
import re
from pathlib import Path

import numpy as np

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import VECTOR_STORE_DIR, RETRIEVAL_TOP_K

log = logging.getLogger(__name__)

EMBEDDINGS_FILE = VECTOR_STORE_DIR / "embeddings.npy"
METADATA_FILE   = VECTOR_STORE_DIR / "metadata.json"
IDS_FILE        = VECTOR_STORE_DIR / "ids.json"

# Stato in memoria (caricato una volta sola)
_embeddings: np.ndarray | None = None   # shape (N, D)
_metadata: list[dict] = []
_ids: list[str] = []
_id_to_idx: dict[str, int] = {}
_bm25 = None  # indice BM25, ricostruito quando il corpus cambia


def _ensure_loaded():
    global _embeddings, _metadata, _ids, _id_to_idx
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
            log.info(f"Vector store caricato: {len(_ids)} chunk, dim={_embeddings.shape[1]}")
            _build_bm25()
        except Exception as e:
            log.warning(f"Errore caricamento vector store: {e} — parto da zero")
            _reset_state()
    else:
        _reset_state()


def _reset_state():
    global _embeddings, _metadata, _ids, _id_to_idx, _bm25
    _embeddings = None  # dimensione determinata dal primo batch
    _metadata = []
    _ids = []
    _id_to_idx = {}
    _bm25 = None


def _tokenize(text: str) -> list[str]:
    """Tokenizzazione semplice per BM25: lowercase, solo parole."""
    return re.findall(r"[a-zàáâäèéêëìíîïòóôöùúûü]+", text.lower())


def _build_bm25():
    """Costruisce o ricostruisce l'indice BM25 dal corpus corrente."""
    global _bm25
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


def _atomic_write_json(path: Path, data):
    """Scrive JSON in modo atomico: prima su .tmp, poi rinomina."""
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    tmp.replace(path)  # atomico su tutti i filesystem POSIX


def _save():
    VECTOR_STORE_DIR.mkdir(parents=True, exist_ok=True)
    # embeddings.npy: numpy scrive già su file temporaneo interno
    np.save(str(EMBEDDINGS_FILE), _embeddings)
    _atomic_write_json(METADATA_FILE, _metadata)
    _atomic_write_json(IDS_FILE, _ids)
    _build_bm25()


def chunk_id(text: str, source: str, chunk_index: int) -> str:
    raw = f"{source}::{chunk_index}::{text[:64]}"
    return hashlib.md5(raw.encode()).hexdigest()


def upsert_chunks(chunks: list[dict], embeddings: list[list[float]]) -> int:
    """
    Inserisce o aggiorna chunk nel vector store.
    chunks: lista di {"text": str, "metadata": dict}
    Ritorna il numero di chunk inseriti/aggiornati.
    """
    global _embeddings, _metadata, _ids, _id_to_idx

    if not chunks:
        return 0

    _ensure_loaded()

    inserted = 0
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
        norm_vec = np.array(vec, dtype=np.float32)
        n = np.linalg.norm(norm_vec)
        if n > 0:
            norm_vec = norm_vec / n

        if cid in _id_to_idx:
            # Aggiorna in place
            idx = _id_to_idx[cid]
            _embeddings[idx] = norm_vec
            _metadata[idx] = {**chunk["metadata"], "text": chunk["text"]}
        else:
            new_vecs.append(norm_vec)
            new_meta.append({**chunk["metadata"], "text": chunk["text"]})
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

    _save()
    return inserted


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


def is_bm25_active() -> bool:
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


def remove_sources(stale: set[str]) -> int:
    """Rimuove tutti i chunk le cui sorgenti non sono più nel crawl corrente.
    Ritorna il numero di chunk rimossi."""
    global _embeddings, _metadata, _ids, _id_to_idx
    if not stale:
        return 0
    _ensure_loaded()
    keep = [i for i, m in enumerate(_metadata) if m.get("source") not in stale]
    removed = len(_metadata) - len(keep)
    if removed == 0:
        return 0
    _embeddings = _embeddings[keep]
    _metadata   = [_metadata[i] for i in keep]
    _ids        = [_ids[i] for i in keep]
    _id_to_idx  = {id_: i for i, id_ in enumerate(_ids)}
    _save()
    log.info(f"Rimossi {removed} chunk da {len(stale)} sorgenti stale.")
    return removed


def clear_collection():
    global _embeddings, _metadata, _ids, _id_to_idx
    _reset_state()
    _save()
    log.info("Vector store svuotato.")
