"""
Motore RAG: riceve una query, recupera i chunk rilevanti dal vector store
e genera una risposta tramite LLM (Ollama o Claude API).

Pipeline di retrieval:
  1. Query expansion — sinonimi e varianti per query municipali
  2. Hybrid search — cosine similarity + BM25 con RRF
  3. Re-ranking — boost titolo + penalità duplicati
"""

import logging
import re
from typing import AsyncGenerator

import httpx

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import (
    LLM_PROVIDER,
    OLLAMA_BASE_URL, OLLAMA_MODEL,
    ANTHROPIC_API_KEY, CLAUDE_MODEL,
    SYSTEM_PROMPT_IT, SYSTEM_PROMPT_EN,
    RETRIEVAL_TOP_K,
    SITE_URL,
)
from indexer.embedder import embed_query
from indexer.vector_store import hybrid_search

log = logging.getLogger(__name__)

# Soglia minima di similarità per includere un chunk nel contesto
MIN_SIMILARITY = 0.38

# Mappa keyword → ufficio competente — caricata da config/offices.json
def _load_office_map() -> list[tuple[set, str, str]]:
    import json as _j
    f = Path(__file__).parent.parent / "config" / "offices.json"
    if not f.exists():
        return []
    try:
        entries = _j.loads(f.read_text(encoding="utf-8"))
        result = []
        for e in entries:
            url = SITE_URL.rstrip("/") + e["path"] if e.get("path") else e.get("url", "")
            result.append((set(e["keywords"]), e["name"], url))
        return result
    except Exception as exc:
        log.warning(f"Impossibile caricare offices.json: {exc}")
        return []

_OFFICE_MAP = _load_office_map()


def suggest_office(query: str) -> str | None:
    """Suggerisce l'ufficio competente in base alle parole chiave della query."""
    query_lower = query.lower()
    for keywords, office_name, office_url in _OFFICE_MAP:
        if any(kw in query_lower for kw in keywords):
            return f"Per questo argomento puoi contattare: **{office_name}** — {office_url}"
    return None

# Sinonimi per espansione query — caricati da config/synonyms.json.
# Il file è specifico per il dominio dell'organizzazione che usa il sistema.
# Se il file non esiste, nessuna espansione viene applicata.
def _load_synonyms() -> dict[str, list[str]]:
    import json as _j
    f = Path(__file__).parent.parent / "config" / "synonyms.json"
    if not f.exists():
        return {}
    try:
        return _j.loads(f.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning(f"Impossibile caricare synonyms.json: {exc}")
        return {}

_SYNONYMS = _load_synonyms()


def expand_query(query: str) -> str:
    """
    Espande la query con sinonimi del dominio municipale.
    Restituisce la query originale + termini correlati.
    """
    query_lower = query.lower()
    expansions = []
    for term, synonyms in _SYNONYMS.items():
        if term in query_lower:
            expansions.extend(synonyms)
    if expansions:
        return query + " " + " ".join(expansions)
    return query


def rerank(chunks: list[dict], query: str) -> list[dict]:
    """
    Re-ranking leggero senza modello aggiuntivo:
    - Boost se termini della query compaiono nel titolo
    - Boost se la categoria è "servizio" (più utile per l'utente)
    - Penalità per chunk duplicati (stesso source + testo simile)
    """
    query_terms = set(re.findall(r"\w+", query.lower()))

    seen_sources: dict[str, int] = {}
    scored = []

    for chunk in chunks:
        score = chunk["score"]

        # Boost titolo
        title_terms = set(re.findall(r"\w+", chunk.get("title", "").lower()))
        title_overlap = len(query_terms & title_terms)
        score += title_overlap * 0.02

        # Boost categoria servizio
        if chunk.get("category") == "servizio":
            score += 0.01

        # Penalità duplicati stessa fonte
        src = chunk.get("source", "")
        if src in seen_sources:
            score -= 0.05 * seen_sources[src]
        seen_sources[src] = seen_sources.get(src, 0) + 1

        scored.append((score, chunk))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored]


# Fatti noti sempre disponibili, indipendenti dal retrieval.
# Utile per informazioni di base che l'embedding non recupera bene
# (indirizzi, orari, contatti istituzionali). Caricati da config/known_facts.json.
def _load_known_facts() -> list[dict]:
    import json as _j
    f = Path(__file__).parent.parent / "config" / "known_facts.json"
    if not f.exists():
        return []
    try:
        entries = _j.loads(f.read_text(encoding="utf-8"))
        result = []
        for e in entries:
            source = e.get("source") or (SITE_URL.rstrip("/") + e["source_path"]) if e.get("source_path") else ""
            result.append({
                "keywords":    set(e["keywords"]),
                "require_any": set(e.get("require_any", e["keywords"])),
                "chunk": {
                    "text":     e["text"],
                    "title":    e["title"],
                    "source":   source,
                    "score":    1.0,
                    "category": e.get("category", "pagina"),
                },
            })
        return result
    except Exception as exc:
        log.warning(f"Impossibile caricare known_facts.json: {exc}")
        return []

_KNOWN_FACTS = _load_known_facts()


def _inject_known_facts(query: str) -> list[dict]:
    """Restituisce i fatti noti rilevanti per la query."""
    q = set(re.findall(r"\w+", query.lower()))
    results = []
    for fact in _KNOWN_FACTS:
        keywords = fact["keywords"]
        require_any = fact.get("require_any", keywords)
        if q & keywords and q & require_any:
            results.append(fact["chunk"])
    return results


def _dedup_chunks(chunks: list[dict]) -> list[dict]:
    """Rimuove chunk con testo identico, mantenendo il primo (score più alto)."""
    seen_texts: set[str] = set()
    result = []
    for chunk in chunks:
        text = chunk.get("text", "").strip()
        if text and text not in seen_texts:
            seen_texts.add(text)
            result.append(chunk)
    return result


def retrieve_context(query: str, top_k: int = RETRIEVAL_TOP_K) -> list[dict]:
    """
    Recupera i chunk più rilevanti per la query.
    Pipeline: known facts → expand → hybrid search → dedup → filter → rerank
    """
    known = _inject_known_facts(query)

    expanded_query = expand_query(query)
    query_vec = embed_query(expanded_query)

    # Rileva fallimento embedding (vettore zero) — usa BM25 puro
    vec_is_zero = all(v == 0.0 for v in query_vec[:10])
    if vec_is_zero:
        log.warning("Embedding zero — fallback BM25 puro per retrieval")
        chunks = hybrid_search(query_vec, expanded_query, top_k=top_k * 3,
                               vector_weight=0.0, bm25_weight=1.0)
        chunks = _dedup_chunks(chunks)
        # Soglia più bassa per BM25 puro (scale diverse da cosine similarity)
        chunks = [c for c in chunks if c["score"] >= 0.005]
    else:
        chunks = hybrid_search(query_vec, expanded_query, top_k=top_k * 3,
                               vector_weight=0.4, bm25_weight=0.6)
        chunks = _dedup_chunks(chunks)
        chunks = [c for c in chunks if c["score"] >= MIN_SIMILARITY]
    chunks = rerank(chunks, query)

    # Anteponi i fatti noti evitando duplicati per source
    known_sources = {k["source"] for k in known}
    chunks = [c for c in chunks if c.get("source") not in known_sources]
    return (known + chunks)[:top_k]


def build_context_block(chunks: list[dict]) -> str:
    """Formatta i chunk con metadati arricchiti per il prompt."""
    if not chunks:
        return ""
    parts = []
    for i, chunk in enumerate(chunks, 1):
        # Etichetta arricchita con metadati
        label_parts = [f"Fonte {i}: {chunk['title']}"]
        if chunk.get("category"):
            label_parts.append(f"tipo={chunk['category']}")
        if chunk.get("service_status"):
            label_parts.append(f"stato={chunk['service_status']}")
        if chunk.get("date"):
            label_parts.append(f"aggiornato={chunk['date']}")
        label_parts.append(chunk["source"])
        source_label = f"[{' — '.join(label_parts)}]"
        parts.append(f"{source_label}\n{chunk['text']}")
    return "\n\n---\n\n".join(parts)


def build_prompt(
    query: str,
    context: str,
    language: str = "it",
    history: list[dict] | None = None,
) -> list[dict]:
    """
    Costruisce i messaggi per il LLM nel formato chat.
    Supporta history conversazionale (lista di {"role", "content"}).
    """
    system = SYSTEM_PROMPT_IT if language == "it" else SYSTEM_PROMPT_EN

    if context:
        user_content = (
            f"CONTESTO (estratto dalla base di conoscenza — usa SOLO queste informazioni):\n\n"
            f"{context}\n\n"
            f"---\n\n"
            f"DOMANDA: {query}"
        )
    else:
        office_hint = suggest_office(query)
        office_text = f"\n\n{office_hint}" if office_hint else ""
        user_content = (
            f"DOMANDA: {query}\n\n"
            f"(Non ho trovato nella base di conoscenza informazioni specifiche "
            f"su questo argomento. Dillo chiaramente all'utente.{office_text})"
        )

    messages = [{"role": "system", "content": system}]

    # Aggiungi storia conversazionale (max 3 turni)
    if history:
        messages.extend(history[-6:])  # max 3 turni = 6 messaggi

    messages.append({"role": "user", "content": user_content})
    return messages


def detect_language(text: str) -> str:
    """
    Rileva la lingua della query (euristica semplice).
    Ritorna 'it' o 'en'.
    """
    it_words = {"ciao", "come", "dove", "quando", "cosa", "chi", "perché",
                "orario", "ufficio", "comune", "servizio", "documenti",
                "informazioni", "grazie", "buongiorno", "buonasera"}
    en_words = {"hello", "how", "where", "when", "what", "who", "why",
                "office", "service", "documents", "information", "thanks",
                "opening", "hours", "tourist"}
    text_lower = text.lower()
    words = set(text_lower.split())
    it_score = len(words & it_words)
    en_score = len(words & en_words)
    return "en" if en_score > it_score else "it"


# ---------------------------------------------------------------------------
# Generazione con Ollama
# ---------------------------------------------------------------------------

async def generate_ollama(messages: list[dict]) -> str:
    """Chiama Ollama in modalità non-streaming e ritorna la risposta completa."""
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": 0.3,
            "top_p": 0.9,
            "num_predict": 1024,
            "num_ctx": 4096,
        },
    }
    async with httpx.AsyncClient(timeout=600.0) as client:
        resp = await client.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["message"]["content"].strip()


async def stream_ollama(messages: list[dict]) -> AsyncGenerator[str, None]:
    """Chiama Ollama in streaming, yield di token progressivi. Ritenta una volta su 500."""
    import asyncio as _asyncio
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": True,
        "options": {"temperature": 0.3, "top_p": 0.9, "num_predict": 512, "num_ctx": 3072},
    }
    last_exc: Exception = RuntimeError("Ollama non disponibile")
    for attempt in range(2):
        if attempt > 0:
            await _asyncio.sleep(3)
        try:
            async with httpx.AsyncClient(timeout=600.0) as client:
                async with client.stream("POST", f"{OLLAMA_BASE_URL}/api/chat",
                                          json=payload) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        try:
                            chunk = _json.loads(line)
                            token = chunk.get("message", {}).get("content", "")
                            if token:
                                yield token
                            if chunk.get("done"):
                                return
                        except _json.JSONDecodeError:
                            continue
                    return
        except httpx.HTTPStatusError as e:
            last_exc = e
            log.warning(f"Ollama HTTP {e.response.status_code} (tentativo {attempt+1}/2)")
            continue
        except Exception:
            raise
    raise last_exc


# ---------------------------------------------------------------------------
# Generazione con Claude API
# ---------------------------------------------------------------------------

async def generate_claude(messages: list[dict]) -> str:
    """Chiama Claude API e ritorna la risposta completa."""
    import anthropic
    system_content = next(
        (m["content"] for m in messages if m["role"] == "system"), ""
    )
    user_messages = [m for m in messages if m["role"] != "system"]

    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    response = await client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1024,
        system=system_content,
        messages=user_messages,
        temperature=0.3,
    )
    try:
        if response.usage:
            _record_tokens(response.usage.input_tokens, response.usage.output_tokens, CLAUDE_MODEL)
    except Exception:
        pass
    return response.content[0].text.strip()


async def stream_claude(messages: list[dict]) -> AsyncGenerator[str, None]:
    """Chiama Claude API in streaming."""
    import anthropic
    system_content = next(
        (m["content"] for m in messages if m["role"] == "system"), ""
    )
    user_messages = [m for m in messages if m["role"] != "system"]

    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    async with client.messages.stream(
        model=CLAUDE_MODEL,
        max_tokens=1024,
        system=system_content,
        messages=user_messages,
        temperature=0.3,
    ) as stream:
        async for text in stream.text_stream:
            yield text
        try:
            final = await stream.get_final_message()
            if final and final.usage:
                _record_tokens(final.usage.input_tokens, final.usage.output_tokens, CLAUDE_MODEL)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Cache risposte (in-memory, LRU semplice)
# ---------------------------------------------------------------------------

import hashlib as _hashlib
import json as _json
import datetime as _datetime
import time as _time
from collections import OrderedDict, Counter as _Counter
from pathlib import Path as _Path

# Log query senza risposta per identificare gap di contenuto
_GAPS_LOG = _Path(__file__).parent.parent / "data" / "gaps.jsonl"


def _log_gap(query: str, chunks_found: int):
    """Registra query con 0 chunk o risposta vuota (senza dati personali)."""
    try:
        _GAPS_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": _datetime.datetime.now().isoformat(timespec="seconds"),
            "query": query[:200],
            "chunks": chunks_found,
        }
        with open(_GAPS_LOG, "a", encoding="utf-8") as f:
            f.write(_json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass

_CACHE_MAX = 200  # max risposte cachate
_response_cache: OrderedDict = OrderedDict()

_STATS_FILE = _Path(__file__).parent.parent / "data" / "stats.json"

_query_count: int = 0
_response_times: list[float] = []
_query_freq: _Counter = _Counter()
_hour_counts: list[int] = [0] * 24

# Token tracking (solo per provider Claude)
_token_in_total: int = 0
_token_out_total: int = 0
_token_cost_total: float = 0.0
_token_history: list[dict] = []   # ultimi 100 record
_TOKEN_HISTORY_MAX = 100

# Prezzi USD per milione di token (input, output)
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-7":   (15.00, 75.00),
    "claude-sonnet-4-6":  (3.00, 15.00),
    "claude-haiku-4-5":   (0.80,  4.00),
}


def _cost_usd(in_tok: int, out_tok: int, model: str) -> float:
    for prefix, (p_in, p_out) in _MODEL_PRICING.items():
        if model.startswith(prefix):
            return (in_tok * p_in + out_tok * p_out) / 1_000_000
    return 0.0


def _record_tokens(in_tok: int, out_tok: int, model: str) -> None:
    global _token_in_total, _token_out_total, _token_cost_total
    cost = _cost_usd(in_tok, out_tok, model)
    _token_in_total  += in_tok
    _token_out_total += out_tok
    _token_cost_total += cost
    _token_history.append({
        "ts":    _datetime.datetime.now().isoformat(timespec="seconds"),
        "in":    in_tok,
        "out":   out_tok,
        "model": model,
        "cost":  round(cost, 6),
    })
    if len(_token_history) > _TOKEN_HISTORY_MAX:
        _token_history.pop(0)
    _save_stats()  # persisti subito: per lo streaming i token arrivano dopo _save_stats() di answer()


def _load_stats():
    global _query_count, _response_times, _query_freq, _hour_counts
    global _token_in_total, _token_out_total, _token_cost_total, _token_history
    if not _STATS_FILE.exists():
        return
    try:
        with open(_STATS_FILE, encoding="utf-8") as f:
            d = _json.load(f)
        _query_count    = d.get("query_count", 0)
        _response_times = d.get("response_times", [])[-100:]
        _query_freq     = _Counter(d.get("query_freq", {}))
        hc = d.get("hour_counts", [0] * 24)
        _hour_counts    = (hc + [0] * 24)[:24]
        _token_in_total   = d.get("token_in_total", 0)
        _token_out_total  = d.get("token_out_total", 0)
        _token_cost_total = d.get("token_cost_total", 0.0)
        _token_history    = d.get("token_history", [])[-_TOKEN_HISTORY_MAX:]
    except Exception as e:
        log.warning(f"Impossibile caricare stats: {e}")


def _save_stats():
    try:
        _STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_STATS_FILE, "w", encoding="utf-8") as f:
            _json.dump({
                "query_count":      _query_count,
                "response_times":   _response_times[-100:],
                "query_freq":       dict(_query_freq.most_common(100)),
                "hour_counts":      _hour_counts,
                "token_in_total":   _token_in_total,
                "token_out_total":  _token_out_total,
                "token_cost_total": _token_cost_total,
                "token_history":    _token_history[-_TOKEN_HISTORY_MAX:],
            }, f)
    except Exception as e:
        log.warning(f"Impossibile salvare stats: {e}")


_load_stats()


def get_query_count() -> int:
    return _query_count


def get_activity_stats() -> dict:
    avg_ms = int(sum(_response_times) / len(_response_times) * 1000) if _response_times else None
    top_q = [(q, c) for q, c in _query_freq.most_common(5)]
    n = len(_token_history)
    avg_in  = int(_token_in_total  / n) if n else None
    avg_out = int(_token_out_total / n) if n else None
    return {
        "avg_response_ms":  avg_ms,
        "top_queries":      top_q,
        "hour_counts":      _hour_counts,
        "token_in_total":   _token_in_total,
        "token_out_total":  _token_out_total,
        "token_cost_total": round(_token_cost_total, 4),
        "token_avg_in":     avg_in,
        "token_avg_out":    avg_out,
        "token_history":    _token_history[-20:],
    }


def _cache_key(query: str) -> str:
    return _hashlib.md5(query.lower().strip().encode()).hexdigest()


def _cache_get(query: str):
    key = _cache_key(query)
    if key in _response_cache:
        _response_cache.move_to_end(key)
        return _response_cache[key]
    return None


def _cache_set(query: str, value):
    key = _cache_key(query)
    _response_cache[key] = value
    _response_cache.move_to_end(key)
    if len(_response_cache) > _CACHE_MAX:
        _response_cache.popitem(last=False)


# ---------------------------------------------------------------------------
# Interfaccia pubblica
# ---------------------------------------------------------------------------

async def answer(
    query: str,
    stream: bool = False,
    history: list[dict] | None = None,
):
    """
    Risponde a una domanda usando RAG.

    Se stream=False: ritorna dict con risposta e fonti.
    Se stream=True: ritorna (AsyncGenerator[str], fonti).

    history: lista di {"role": "user"|"assistant", "content": str}
             per mantenere il contesto conversazionale (max 3 turni).
    """
    global _query_count
    query = query.strip()
    if not query:
        raise ValueError("Query vuota")
    _query_count += 1
    _hour_counts[_datetime.datetime.now().hour] += 1
    _norm = re.sub(r"\s+", " ", query.lower().strip("?! "))
    _query_freq[_norm] += 1

    # Cache solo per query senza history (conversazioni stateless)
    use_cache = not history and not stream
    if use_cache:
        cached = _cache_get(query)
        if cached:
            log.info("Cache hit: '%s'", query[:60].replace('\n', ' ').replace('\r', ' '))
            return cached

    language = detect_language(query)
    chunks = retrieve_context(query)
    context = build_context_block(chunks)
    messages = build_prompt(query, context, language, history=history)

    sources = [
        {"title": c["title"], "url": c["source"], "score": c["score"]}
        for c in chunks
    ]
    seen = set()
    unique_sources = []
    for s in sources:
        if s["url"] not in seen:
            seen.add(s["url"])
            unique_sources.append(s)

    _safe_q = query[:60].replace('\n', ' ').replace('\r', ' ')
    log.info("Query: '%s...' | lang=%s | chunks=%d | provider=%s",
             _safe_q, language, len(chunks), LLM_PROVIDER)

    if len(chunks) == 0:
        _log_gap(query, 0)

    if stream:
        if not chunks:
            # Nessun contesto trovato — bypassa il LLM con fallback affidabile
            office_hint = suggest_office(query)
            fallback = (
                "Non ho trovato informazioni specifiche su questo argomento "
                "nella base di conoscenza."
            )
            if office_hint:
                fallback += f"\n\n{office_hint}"

            async def _no_context_gen():
                yield fallback

            _save_stats()
            return _no_context_gen(), unique_sources

        if LLM_PROVIDER == "claude":
            gen = stream_claude(messages)
        else:
            gen = stream_ollama(messages)
        _save_stats()
        return gen, unique_sources

    else:
        _t0 = _time.monotonic()
        if LLM_PROVIDER == "claude":
            text = await generate_claude(messages)
        else:
            text = await generate_ollama(messages)
        _response_times.append(_time.monotonic() - _t0)
        if len(_response_times) > 100:
            _response_times.pop(0)

        result = {"answer": text, "sources": unique_sources, "language": language}
        if use_cache:
            _cache_set(query, result)
        _save_stats()
        return result
