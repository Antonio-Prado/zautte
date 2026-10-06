"""
Controlla che i vettori calcolati da Ollama non cambino, prima e dopo un
aggiornamento di Ollama (o di qualsiasi cosa tocchi gli embedding).

Prende 50 brani a caso dell'indice (seme fisso, solo vettori non nulli) e 10
domande tipo, ne calcola i vettori con l'Ollama in esecuzione e li confronta
(similarità del coseno). Vettori diversi da quelli dell'indice peggiorano la
ricerca finché non si ricalcola tutto (scripts/reembed.py).

Uso (sul server, dalla directory del progetto):
    venv/bin/python -m scripts.embed_check save    /percorso/prima.npz   # prima dell'aggiornamento
    venv/bin/python -m scripts.embed_check compare /percorso/prima.npz   # dopo

`save` stampa anche il confronto con i vettori dell'indice (deve essere 1,0 o
quasi). `compare` esce con 2 se la similarità minima con il salvataggio è
sotto 0,999. Il 6/10/2026, da Ollama 0.19.0 a 0.31.1 con bge-m3: minima
0,999906, media 0,999982, domande identiche; nessun ricalcolo necessario.
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
        np.savez(path, idx=np.array(idx), texts=np.array(texts + QUERIES, dtype=object),
                 stored=stored, vec=vec)
        c = _cos(vec[:N], stored)
        print(f"Ollama attuale vs indice: min {c.min():.6f}  media {c.mean():.6f}")
        return 0
    saved = np.load(path, allow_pickle=True)
    vec = np.array(embed_texts(list(saved["texts"])), dtype=np.float32)
    old = _cos(vec, saved["vec"])
    idx = _cos(vec[:N], saved["stored"])
    print(f"ora vs salvataggio: brani min {old[:N].min():.6f} media {old[:N].mean():.6f}; "
          f"domande min {old[N:].min():.6f} media {old[N:].mean():.6f}")
    print(f"ora vs indice:      brani min {idx.min():.6f} media {idx.mean():.6f}")
    same = float(old.min()) >= THRESHOLD
    print("ESITO:", "UGUALI" if same else f"DIVERSI (minima sotto {THRESHOLD})")
    return 0 if same else 2


if __name__ == "__main__":
    sys.exit(main())
