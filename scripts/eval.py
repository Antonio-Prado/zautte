"""
Valutazione automatica della qualità del RAG.

Esegue un set di domande di test e misura:
  - chunk trovati (retrieval)
  - presenza di parole chiave attese nella risposta
  - tempo di risposta

Uso:
  venv/bin/python -m scripts.eval
  venv/bin/python -m scripts.eval --no-llm   # solo retrieval, più veloce
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from indexer.embedder import embed_query
from indexer.vector_store import hybrid_search
from api.rag import expand_query, retrieve_context, MIN_SIMILARITY

# ---------------------------------------------------------------------------
# Set di domande di test con parole chiave attese nella risposta
# ---------------------------------------------------------------------------

TEST_CASES = [
    {
        "question": "Come si richiede la carta di identità?",
        "expected_keywords": ["carta", "identità", "anagrafe", "documento"],
        "min_chunks": 1,
    },
    {
        "question": "Come si cambia la residenza?",
        "expected_keywords": ["residenza", "anagrafe", "trasferimento"],
        "min_chunks": 1,
    },
    {
        "question": "Come funziona il trasporto scolastico?",
        "expected_keywords": ["scolastico", "trasporto", "iscrizione"],
        "min_chunks": 1,
    },
    {
        "question": "Come si richiede l'accesso agli atti?",
        "expected_keywords": ["accesso", "atti", "richiesta"],
        "min_chunks": 1,
    },
    {
        "question": "Quali sono gli orari della biblioteca?",
        "expected_keywords": ["biblioteca", "orari"],
        "min_chunks": 0,  # potrebbe non essere indicizzata
    },
    {
        "question": "Come si paga la TARI?",
        "expected_keywords": ["tari", "rifiuti", "pagamento"],
        "min_chunks": 1,
    },
    {
        "question": "Come si ottiene il permesso di costruire?",
        "expected_keywords": ["permesso", "costruire", "edilizia", "sue"],
        "min_chunks": 1,
    },
    {
        "question": "Dove si trova la polizia municipale?",
        "expected_keywords": ["polizia", "municipale"],
        "min_chunks": 0,
    },
    {
        "question": "Come si iscrive un figlio all'asilo nido?",
        "expected_keywords": ["asilo", "nido", "iscrizione"],
        "min_chunks": 1,
    },
]


def eval_retrieval(question: str, min_chunks: int) -> tuple[int, float, bool]:
    """Valuta solo il retrieval (senza LLM). Ritorna (n_chunks, tempo_ms, ok)."""
    t0 = time.perf_counter()
    chunks = retrieve_context(question)
    elapsed = (time.perf_counter() - t0) * 1000
    ok = len(chunks) >= min_chunks
    return len(chunks), elapsed, ok


async def eval_full(question: str, expected_keywords: list[str], min_chunks: int) -> dict:
    """Valuta retrieval + risposta LLM."""
    from api.rag import answer
    t0 = time.perf_counter()
    result = await answer(question, stream=False)
    elapsed = (time.perf_counter() - t0) * 1000

    answer_text = result.get("answer", "").lower()
    chunks_found = len(result.get("sources", []))
    keywords_hit = [kw for kw in expected_keywords if kw in answer_text]
    keyword_score = len(keywords_hit) / len(expected_keywords) if expected_keywords else 1.0

    return {
        "question": question,
        "chunks": chunks_found,
        "retrieval_ok": chunks_found >= min_chunks,
        "keywords_hit": keywords_hit,
        "keyword_score": keyword_score,
        "elapsed_ms": int(elapsed),
        "answer_preview": result.get("answer", "")[:120],
    }


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-llm", action="store_true",
                        help="Valuta solo il retrieval senza chiamare il LLM")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"EVAL Zautte — {len(TEST_CASES)} domande di test")
    print(f"Modalità: {'solo retrieval' if args.no_llm else 'retrieval + LLM'}")
    print(f"{'='*60}\n")

    results = []
    for i, tc in enumerate(TEST_CASES, 1):
        q = tc["question"]
        print(f"[{i}/{len(TEST_CASES)}] {q}")

        if args.no_llm:
            n_chunks, elapsed, ok = eval_retrieval(q, tc["min_chunks"])
            r = {
                "question": q,
                "chunks": n_chunks,
                "retrieval_ok": ok,
                "elapsed_ms": int(elapsed),
            }
            status = "OK" if ok else "FAIL"
            print(f"  → {status} | chunks={n_chunks} | {int(elapsed)}ms\n")
        else:
            r = await eval_full(q, tc["expected_keywords"], tc["min_chunks"])
            status = "OK" if r["retrieval_ok"] and r["keyword_score"] >= 0.5 else "FAIL"
            print(f"  → {status} | chunks={r['chunks']} | "
                  f"keywords={r['keyword_score']:.0%} | {r['elapsed_ms']}ms")
            print(f"     {r['answer_preview']}\n")

        results.append(r)

    # Riepilogo
    retrieval_ok = sum(1 for r in results if r["retrieval_ok"])
    print(f"\n{'='*60}")
    print(f"RETRIEVAL OK: {retrieval_ok}/{len(results)}")
    if not args.no_llm:
        kw_avg = sum(r.get("keyword_score", 0) for r in results) / len(results)
        print(f"KEYWORD SCORE medio: {kw_avg:.0%}")
    avg_ms = sum(r["elapsed_ms"] for r in results) / len(results)
    print(f"TEMPO MEDIO: {avg_ms:.0f}ms")
    print(f"{'='*60}\n")

    # Salva risultati
    out = Path(__file__).parent.parent / "data" / "eval_results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"Risultati salvati in {out}")


if __name__ == "__main__":
    asyncio.run(main())
