"""
Ricalcolo di tutti gli embedding del vector store con un altro modello di
Ollama (passaggio da mxbai-embed-large a bge-m3, oppure un aggiornamento di
Ollama che cambia i vettori), senza fermare il servizio.

Fasi:
  build   calcola in una cache su disco i vettori dei testi dello store, in
          parallelo alla produzione (che intanto usa il modello attuale).
          I testi identici (stessa scheda indicizzata con più URL) si calcolano
          una volta sola. Riprendibile: i blocchi già salvati non si ripetono.
  status  dice quanti brani dello store attuale sono coperti dalla cache.
  apply   a sync fermo, riscrive embeddings.npy con i vettori della cache e
          calcola quelli dei brani aggiunti o cambiati dopo il build (ids e
          metadata restano gli stessi). Il vecchio file viene copiato in --backup.
          Subito dopo vanno cambiati OLLAMA_EMBED_MODEL nel .env e riavviata
          l'API: le domande vanno vettorizzate con lo stesso modello dei brani.

Uso sul server (root):
  cd /opt/chatbot
  nice venv/bin/python -m scripts.reembed build --model bge-m3 --cache /data/chatbot/reembed/bge-m3 \\
      --threads 12
  venv/bin/python -m scripts.reembed status --cache /data/chatbot/reembed/bge-m3
  lockf -t 0 /var/run/chatbot-sync.lock venv/bin/python -m scripts.reembed apply \\
      --model bge-m3 --cache /data/chatbot/reembed/bge-m3 --backup /data/chatbot/backups_keep
"""

import argparse
import hashlib
import json
import logging
import shutil
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import EMBEDDING_DIMENSION, OLLAMA_EMBED_MODEL, now_local
from indexer import embedder
from indexer import vector_store as vs

log = logging.getLogger("reembed")

PART_SIZE = 2048  # testi per blocco della cache: ~6 min con bge-m3 sulla CPU attuale


def _key(text: str) -> str:
    """Chiave della cache: il testo come lo riceve Ollama (dopo embedder._clean)."""
    return hashlib.sha1(embedder._clean(text).encode("utf-8")).hexdigest()


def _embed(model: str, texts: list[str]) -> np.ndarray:
    """Vettori normalizzati; righe a zero dove Ollama non ha risposto."""
    embedder.OLLAMA_EMBED_MODEL = model  # letto a ogni richiesta dalle funzioni di embedder
    arr = np.asarray(embedder.embed_texts(texts), dtype=np.float32)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    return np.divide(arr, norms, out=np.zeros_like(arr), where=norms > 0)


def _parts(cache: Path) -> list[tuple[Path, Path]]:
    """Blocchi completi della cache: (chiavi .json, vettori .npy). Il .json si
    scrive per ultimo, quindi un blocco interrotto a metà viene ignorato."""
    return [(k, k.with_suffix(".npy")) for k in sorted(cache.glob("part_*.json"))
            if k.with_suffix(".npy").exists()]


def _cached_keys(cache: Path) -> set[str]:
    keys: set[str] = set()
    for keys_file, _ in _parts(cache):
        keys.update(json.loads(keys_file.read_text(encoding="utf-8")))
    return keys


def _load_cache(cache: Path) -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {}
    for keys_file, vec_file in _parts(cache):
        keys = json.loads(keys_file.read_text(encoding="utf-8"))
        vecs = np.load(vec_file)
        if len(keys) != len(vecs):
            log.warning(f"{keys_file.name}: {len(keys)} chiavi e {len(vecs)} vettori, blocco ignorato")
            continue
        out.update(zip(keys, vecs, strict=True))
    return out


def _save_part(cache: Path, keys: list[str], vecs: np.ndarray) -> None:
    """Aggiunge un blocco alla cache (scrittura atomica, prima i vettori)."""
    numbers = [int(k.stem.split("_")[1]) for k, _ in _parts(cache)]
    stem = cache / f"part_{max(numbers, default=0) + 1:05d}"
    vs._atomic_write_npy(stem.with_suffix(".npy"), vecs)
    vs._atomic_write_json(stem.with_suffix(".json"), keys)


def _store_texts() -> list[str]:
    with open(vs.METADATA_FILE, encoding="utf-8") as f:
        return [m.get("text", "") for m in json.load(f)]


def _check_model(model: str) -> int:
    """Verifica che Ollama risponda col modello e ne restituisce la dimensione."""
    vec = _embed(model, ["prova"])[0]
    if not np.any(vec):
        sys.exit(f"Ollama non calcola embedding con {model} (ollama pull {model}?)")
    return len(vec)


def build(model: str, cache: Path) -> None:
    dim = _check_model(model)
    cache.mkdir(parents=True, exist_ok=True)
    texts: dict[str, str] = {}
    for t in _store_texts():
        texts.setdefault(_key(t), t)
    done = _cached_keys(cache)
    todo = [(k, t) for k, t in texts.items() if k not in done]
    log.info(f"{model} (dim {dim}): {len(texts)} testi distinti, {len(done)} già in cache, "
             f"{len(todo)} da calcolare")

    t0 = time.time()
    failed = 0
    for i in range(0, len(todo), PART_SIZE):
        block = todo[i:i + PART_SIZE]
        vecs = _embed(model, [t for _, t in block])
        ok = np.linalg.norm(vecs, axis=1) > 0
        failed += int((~ok).sum())  # restano fuori dalla cache: li riprova apply
        _save_part(cache, [k for (k, _), good in zip(block, ok, strict=True) if good], vecs[ok])
        n = i + len(block)
        rate = n / (time.time() - t0)
        log.info(f"{n}/{len(todo)} ({rate:.2f} testi/s, ~{(len(todo) - n) / rate / 3600:.1f} h "
                 f"alla fine, falliti {failed})")
    log.info(f"build finito in {(time.time() - t0) / 3600:.1f} h, testi falliti {failed}")


def status(cache: Path) -> None:
    keys = [_key(t) for t in _store_texts()]
    cached = _cached_keys(cache)
    missing = {k for k in keys if k not in cached}
    log.info(f"store: {len(keys)} brani, {len(set(keys))} testi distinti; cache: {len(cached)} "
             f"testi; mancano {len(missing)} testi ({sum(k in missing for k in keys)} brani)")


def apply(model: str, cache: Path, backup: Path) -> None:
    dim = _check_model(model)
    if dim != EMBEDDING_DIMENSION:
        sys.exit(f"{model} ha dimensione {dim}, EMBEDDING_DIMENSION è {EMBEDDING_DIMENSION}")
    with open(vs.METADATA_FILE, encoding="utf-8") as f:
        meta = json.load(f)
    with open(vs.IDS_FILE, encoding="utf-8") as f:
        n_ids = len(json.load(f))
    n_old = np.load(vs.EMBEDDINGS_FILE, mmap_mode="r").shape[0]
    if not len(meta) == n_ids == n_old:
        sys.exit(f"store incoerente: metadata {len(meta)}, ids {n_ids}, vettori {n_old}")

    vectors = _load_cache(cache)
    keys = [_key(m.get("text", "")) for m in meta]
    missing: dict[str, str] = {}
    for k, m in zip(keys, meta, strict=True):
        if k not in vectors:
            missing.setdefault(k, m.get("text", ""))
    log.info(f"store: {len(meta)} brani; dalla cache {len(meta) - sum(k in missing for k in keys)}, "
             f"testi nuovi o cambiati da calcolare {len(missing)}")
    if missing:
        vecs = _embed(model, list(missing.values()))
        ok = np.linalg.norm(vecs, axis=1) > 0
        new_keys = [k for k, good in zip(missing, ok, strict=True) if good]
        _save_part(cache, new_keys, vecs[ok])
        vectors.update(zip(new_keys, vecs[ok], strict=True))

    emb = np.zeros((len(meta), dim), dtype=np.float32)
    flags_changed = 0
    for i, k in enumerate(keys):
        vec = vectors.get(k)
        if vec is not None:
            emb[i] = vec
        # stesso flag di vector_store.upsert_chunks per i vettori zero
        zero = vec is None
        if zero != bool(meta[i].get("needs_reembedding")):
            flags_changed += 1
            if zero:
                meta[i]["needs_reembedding"] = True
            else:
                meta[i].pop("needs_reembedding", None)

    backup.mkdir(parents=True, exist_ok=True)
    dest = backup / f"embeddings_{OLLAMA_EMBED_MODEL}_{now_local():%Y%m%d_%H%M%S}.npy"
    shutil.copy2(vs.EMBEDDINGS_FILE, dest)
    log.info(f"vecchi vettori copiati in {dest}")
    vs._atomic_write_npy(vs.EMBEDDINGS_FILE, emb)
    if flags_changed:
        vs._atomic_write_json(vs.METADATA_FILE, meta)
    zeros = int((~np.any(emb, axis=1)).sum())
    log.info(f"scritto {vs.EMBEDDINGS_FILE}: {emb.shape}, vettori zero {zeros}, "
             f"flag needs_reembedding cambiati {flags_changed}. Ora OLLAMA_EMBED_MODEL={model} "
             "nel .env e riavvio dell'API.")


def main():
    parser = argparse.ArgumentParser(description="Ricalcolo degli embedding con un altro modello")
    parser.add_argument("mode", choices=["build", "status", "apply"])
    parser.add_argument("--model", help="modello di embedding di Ollama (build, apply)")
    parser.add_argument("--cache", type=Path, required=True, help="cartella della cache dei vettori")
    parser.add_argument("--backup", type=Path, help="dove copiare il vecchio embeddings.npy (apply)")
    parser.add_argument("--threads", type=int,
                        help="thread di Ollama (num_thread): meno dei core, per lasciare CPU all'API")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    logging.getLogger("indexer.embedder").setLevel(logging.WARNING)  # una riga per batch da 16
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.mode != "status" and not args.model:
        parser.error("--model è obbligatorio")
    if args.threads:
        embedder.OLLAMA_OPTIONS = {"num_thread": args.threads}
    if args.mode == "build":
        build(args.model, args.cache)
    elif args.mode == "status":
        status(args.cache)
    else:
        if not args.backup:
            parser.error("--backup è obbligatorio con apply")
        apply(args.model, args.cache, args.backup)


if __name__ == "__main__":
    main()
