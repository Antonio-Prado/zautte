"""
Recomputation of all the vector store embeddings with a different Ollama
model (switch from mxbai-embed-large to bge-m3, or an Ollama update
that changes the vectors), without stopping the service.

Phases:
  build   computes on a disk cache the vectors of the store texts, in
          parallel with production (which meanwhile uses the current model).
          Identical texts (same page indexed under several URLs) are computed
          only once. Resumable: blocks already saved are not repeated.
  status  says how many passages of the current store are covered by the cache.
  apply   with sync stopped, rewrites embeddings.npy with the cache vectors and
          computes those of the passages added or changed after the build (ids and
          metadata stay the same). The old file is copied to --backup.
          Right after, OLLAMA_EMBED_MODEL must be changed in .env and the
          API restarted: questions must be vectorised with the same model as the passages.

Usage on the server (root):
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

from api.privdrop import drop_to_sync_user

drop_to_sync_user()   # as root: become the sync account before config.settings reads .env

from config.settings import EMBEDDING_DIMENSION, OLLAMA_EMBED_MODEL, now_local
from indexer import embedder
from indexer import vector_store as vs

log = logging.getLogger("reembed")

PART_SIZE = 2048  # texts per cache block: ~6 min with bge-m3 on the current CPU


def _key(text: str) -> str:
    """Cache key: the text as Ollama receives it (after embedder._clean)."""
    return hashlib.sha1(embedder._clean(text).encode("utf-8")).hexdigest()


def _embed(model: str, texts: list[str]) -> np.ndarray:
    """Normalised vectors; zero rows where Ollama did not answer."""
    embedder.OLLAMA_EMBED_MODEL = model  # read on every request by the embedder functions
    arr = np.asarray(embedder.embed_texts(texts), dtype=np.float32)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    return np.divide(arr, norms, out=np.zeros_like(arr), where=norms > 0)


def _parts(cache: Path) -> list[tuple[Path, Path]]:
    """Complete cache blocks: (.json keys, .npy vectors). The .json is
    written last, so a block interrupted halfway is ignored."""
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
            log.warning(f"{keys_file.name}: {len(keys)} keys and {len(vecs)} vectors, block ignored")
            continue
        out.update(zip(keys, vecs, strict=True))
    return out


def _save_part(cache: Path, keys: list[str], vecs: np.ndarray) -> None:
    """Add a block to the cache (atomic write, vectors first)."""
    numbers = [int(k.stem.split("_")[1]) for k, _ in _parts(cache)]
    stem = cache / f"part_{max(numbers, default=0) + 1:05d}"
    vs._atomic_write_npy(stem.with_suffix(".npy"), vecs)
    vs._atomic_write_json(stem.with_suffix(".json"), keys)


def _store_texts() -> list[str]:
    with open(vs.METADATA_FILE, encoding="utf-8") as f:
        return [m.get("text", "") for m in json.load(f)]


def _check_model(model: str) -> int:
    """Check that Ollama answers with the model and return its dimension."""
    vec = _embed(model, ["prova"])[0]
    if not np.any(vec):
        sys.exit(f"Ollama does not compute embeddings with {model} (ollama pull {model}?)")
    return len(vec)


def build(model: str, cache: Path) -> None:
    dim = _check_model(model)
    cache.mkdir(parents=True, exist_ok=True)
    texts: dict[str, str] = {}
    for t in _store_texts():
        texts.setdefault(_key(t), t)
    done = _cached_keys(cache)
    todo = [(k, t) for k, t in texts.items() if k not in done]
    log.info(f"{model} (dim {dim}): {len(texts)} distinct texts, {len(done)} already cached, "
             f"{len(todo)} to compute")

    t0 = time.time()
    failed = 0
    for i in range(0, len(todo), PART_SIZE):
        block = todo[i:i + PART_SIZE]
        vecs = _embed(model, [t for _, t in block])
        ok = np.linalg.norm(vecs, axis=1) > 0
        failed += int((~ok).sum())  # they stay out of the cache: apply retries them
        _save_part(cache, [k for (k, _), good in zip(block, ok, strict=True) if good], vecs[ok])
        n = i + len(block)
        rate = n / (time.time() - t0)
        log.info(f"{n}/{len(todo)} ({rate:.2f} texts/s, ~{(len(todo) - n) / rate / 3600:.1f} h "
                 f"left, failed {failed})")
    log.info(f"build finished in {(time.time() - t0) / 3600:.1f} h, failed texts {failed}")


def status(cache: Path) -> None:
    keys = [_key(t) for t in _store_texts()]
    cached = _cached_keys(cache)
    missing = {k for k in keys if k not in cached}
    log.info(f"store: {len(keys)} chunks, {len(set(keys))} distinct texts; cache: {len(cached)} "
             f"texts; {len(missing)} texts missing ({sum(k in missing for k in keys)} chunks)")


def apply(model: str, cache: Path, backup: Path) -> None:
    dim = _check_model(model)
    if dim != EMBEDDING_DIMENSION:
        sys.exit(f"{model} has dimension {dim}, EMBEDDING_DIMENSION is {EMBEDDING_DIMENSION}")
    with open(vs.METADATA_FILE, encoding="utf-8") as f:
        meta = json.load(f)
    with open(vs.IDS_FILE, encoding="utf-8") as f:
        n_ids = len(json.load(f))
    n_old = np.load(vs.EMBEDDINGS_FILE, mmap_mode="r").shape[0]
    if not len(meta) == n_ids == n_old:
        sys.exit(f"inconsistent store: metadata {len(meta)}, ids {n_ids}, vectors {n_old}")

    vectors = _load_cache(cache)
    keys = [_key(m.get("text", "")) for m in meta]
    missing: dict[str, str] = {}
    for k, m in zip(keys, meta, strict=True):
        if k not in vectors:
            missing.setdefault(k, m.get("text", ""))
    log.info(f"store: {len(meta)} chunks; from cache {len(meta) - sum(k in missing for k in keys)}, "
             f"new or changed texts to compute {len(missing)}")
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
        # same flag as vector_store.upsert_chunks for zero vectors
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
    log.info(f"old vectors copied to {dest}")
    vs._atomic_write_npy(vs.EMBEDDINGS_FILE, emb)
    if flags_changed:
        vs._atomic_write_json(vs.METADATA_FILE, meta)
    vs.write_manifest()
    zeros = int((~np.any(emb, axis=1)).sum())
    log.info(f"written {vs.EMBEDDINGS_FILE}: {emb.shape}, zero vectors {zeros}, "
             f"needs_reembedding flags changed {flags_changed}. Now set OLLAMA_EMBED_MODEL={model} "
             "in .env and restart the API.")


def main():
    parser = argparse.ArgumentParser(description="Recompute embeddings with another model")
    parser.add_argument("mode", choices=["build", "status", "apply"])
    parser.add_argument("--model", help="Ollama embedding model (build, apply)")
    parser.add_argument("--cache", type=Path, required=True, help="vector cache folder")
    parser.add_argument("--backup", type=Path, help="where to copy the old embeddings.npy (apply)")
    parser.add_argument("--threads", type=int,
                        help="Ollama threads (num_thread): fewer than the cores, to leave CPU for the API")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    logging.getLogger("indexer.embedder").setLevel(logging.WARNING)  # one line per batch of 16
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.mode != "status" and not args.model:
        parser.error("--model is required")
    if args.threads:
        embedder.OLLAMA_OPTIONS = {"num_thread": args.threads}
    if args.mode == "build":
        build(args.model, args.cache)
    elif args.mode == "status":
        status(args.cache)
    else:
        if not args.backup:
            parser.error("--backup is required with apply")
        apply(args.model, args.cache, args.backup)


if __name__ == "__main__":
    main()
