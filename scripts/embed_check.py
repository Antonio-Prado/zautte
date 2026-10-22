"""
Checks that the vectors computed by Ollama do not change, before and after an
Ollama update (or anything that touches the embeddings).

It takes 50 random passages from the index (fixed seed, non-zero vectors only) and 10
sample questions, computes their vectors with the running Ollama and compares them
(cosine similarity). Vectors different from those in the index degrade the
search until everything is recomputed (scripts/reembed.py).

Usage (on the server, from the project directory):
    venv/bin/python -m scripts.embed_check save    /path/prima.npz   # before the update
    venv/bin/python -m scripts.embed_check compare /path/prima.npz   # after

`save` also prints the comparison with the index vectors (it must be 1.0 or
almost). `compare` exits with 2 if the minimum similarity with the saved file is
below 0.999. On 6/10/2026, from Ollama 0.19.0 to 0.31.1 with bge-m3: minimum
0.999906, mean 0.999982, identical questions; no recomputation needed.
"""

import json
import random
import sys

import numpy as np

from config.settings import VECTOR_STORE_DIR
from indexer.embedder import embed_texts

N = 50
THRESHOLD = 0.999
QUERIES = [
    "Come rinnovo la carta d'identità?", "orari ufficio tributi", "Bando buono affitti",
    "Come si iscrive un bambino al nido?", "TARI scadenze 2026", "Dove si paga la mensa scolastica?",
    "permesso ZTL residenti", "cambio di residenza", "How do I renew my identity card?",
    "segnalare una buca in strada",
]


def _cos(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    a = a / np.linalg.norm(a, axis=1, keepdims=True)
    b = b / np.linalg.norm(b, axis=1, keepdims=True)
    return (a * b).sum(axis=1)


def _sample() -> tuple[list[int], list[str], np.ndarray]:
    emb = np.load(VECTOR_STORE_DIR / "embeddings.npy", mmap_mode="r")
    with open(VECTOR_STORE_DIR / "metadata.json", encoding="utf-8") as f:
        meta = json.load(f)
    rnd = random.Random(20261006)
    idx: list[int] = []
    while len(idx) < N:
        i = rnd.randrange(len(meta))
        if i not in idx and meta[i].get("text") and float(np.linalg.norm(emb[i])) > 0:
            idx.append(i)
    return idx, [meta[i]["text"] for i in idx], np.array([emb[i] for i in idx], dtype=np.float32)


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in ("save", "compare"):
        print(__doc__)
        return 1
    mode, path = sys.argv[1], sys.argv[2]
    if mode == "save":
        idx, texts, stored = _sample()
        vec = np.array(embed_texts(texts + QUERIES), dtype=np.float32)
        np.savez(path, idx=np.array(idx), texts=np.array(texts + QUERIES),
                 stored=stored, vec=vec)
        c = _cos(vec[:N], stored)
        print(f"Current Ollama vs index: min {c.min():.6f}  mean {c.mean():.6f}")
        return 0
    saved = np.load(path)   # no pickle: the file is only arrays and strings
    vec = np.array(embed_texts(list(saved["texts"])), dtype=np.float32)
    old = _cos(vec, saved["vec"])
    idx = _cos(vec[:N], saved["stored"])
    print(f"now vs saved: chunks min {old[:N].min():.6f} mean {old[:N].mean():.6f}; "
          f"domande min {old[N:].min():.6f} media {old[N:].mean():.6f}")
    print(f"now vs index:       chunks min {idx.min():.6f} mean {idx.mean():.6f}")
    same = float(old.min()) >= THRESHOLD
    print("ESITO:", "UGUALI" if same else f"DIVERSI (minima sotto {THRESHOLD})")
    return 0 if same else 2


if __name__ == "__main__":
    sys.exit(main())
