"""
RAG engine: receives a query, retrieves the relevant chunks from the vector store
and generates an answer through an LLM (Ollama or Claude API).

Retrieval pipeline:
  1. Query expansion — synonyms and variants for municipal queries
  2. Hybrid search — cosine similarity + BM25 with RRF
  3. Re-ranking — title boost + duplicate penalty
"""

import asyncio
import logging
import re
import sys
from collections.abc import AsyncGenerator
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))

from api import smalltalk
from api.filelock import file_lock
from api.safefile import write_text_atomic
from config.settings import (
    ANTHROPIC_API_KEY,
    BEDROCK_AWS_REGION,
    BEDROCK_MODEL,
    BEDROCK_REWRITE_MODEL,
    CLAUDE_MODEL,
    CLAUDE_REWRITE_MODEL,
    LLM_EFFORT,
    LLM_PROVIDER,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    QUERY_REWRITE,
    RETENTION_STATS_DAYS,
    RETRIEVAL_TOP_K,
    SITE_URL,
    SYSTEM_PROMPT_EN,
    SYSTEM_PROMPT_IT,
    is_migrated_source,
    now_local,
)
from indexer.embedder import embed_query
from indexer.vector_store import hybrid_search

log = logging.getLogger(__name__)

# Claude models: direct Anthropic API ("claude") or AWS Bedrock ("bedrock")
_USES_CLAUDE = LLM_PROVIDER in ("claude", "bedrock")
_ANSWER_MODEL = BEDROCK_MODEL if LLM_PROVIDER == "bedrock" else CLAUDE_MODEL
_REWRITE_MODEL = BEDROCK_REWRITE_MODEL if LLM_PROVIDER == "bedrock" else CLAUDE_REWRITE_MODEL
_claude_client_instance = None

# Models that reject temperature (400) and control reasoning with
# output_config.effort; reasoning counts toward max_tokens, so headroom is needed.
_EFFORT_MODELS = ("claude-sonnet-5", "claude-opus-5", "claude-opus-4-7", "claude-opus-4-8", "claude-fable")
_THINKING_MARGIN = 3072
_REFUSAL_TEXT = ("Non posso rispondere a questa richiesta. Per informazioni puoi rivolgerti "
                 "agli uffici comunali.")


def _model_options(model: str, temperature: float, max_tokens: int) -> dict:
    """Request parameters that depend on the model. temperature goes in
    extra_body because anthropic 1.x removed it from the signature (also works with 0.x)."""
    base = model.split("anthropic.", 1)[1] if "anthropic." in model else model
    if base.startswith(_EFFORT_MODELS):
        return {"max_tokens": max_tokens + _THINKING_MARGIN,
                "extra_body": {"output_config": {"effort": LLM_EFFORT}}}
    return {"max_tokens": max_tokens, "extra_body": {"temperature": temperature}}


def _text_of(message) -> str:
    """Answer text: reasoning blocks (even empty ones) may precede the text."""
    return "".join(b.text for b in message.content if getattr(b, "type", "") == "text").strip()


def _claude_client():
    """Async client for the Claude models, created once per process."""
    global _claude_client_instance
    if _claude_client_instance is None:
        import anthropic
        if LLM_PROVIDER == "bedrock":
            _claude_client_instance = anthropic.AsyncAnthropicBedrock(aws_region=BEDROCK_AWS_REGION)
        else:
            _claude_client_instance = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    return _claude_client_instance

# Thresholds and weights depend on the embedding model: tuned for bge-m3 on 04/10/2026
# on 100 questions with a correct page and 12 off-topic ones (with mxbai-embed-large they were
# 0.38 / 0.55 / weights 0.4-0.6, but mxbai gave ~0.70 even to off-topic questions).
# bge-m3 cosine of the best passage: on-topic questions 0.56-0.70, off-topic 0.38-0.50,
# random passages ~0.35.
# Check on the full index (05/10/2026, 222,480 passages): correct page within the 7
# passages 80/100 (mxbai 58); best off-topic passage p50 0.49 / p90 0.53, so
# 0.45 let through 5.5 off-topic passages out of 7 and 0.50 lets through 1.9 (80 → 79 on-topic).

# Minimum similarity threshold to include a chunk in the context
MIN_SIMILARITY = 0.50

# Retrieval "confidence" threshold: if even the best chunk stays below
# this cosine similarity value, the answer is probably built on
# documents that are only thematically close (e.g. budgets for a service question)
# and not really relevant. It is used to log these cases in gaps.jsonl for
# review — it does not alter the answer.
RETRIEVAL_CONFIDENCE = 0.52

# RRF fusion weights between vector search and BM25 (sum 1)
VECTOR_WEIGHT = 0.6
BM25_WEIGHT = 0.4

# PDF passage penalty in re-ranking: at similar relevance the site
# pages, written for citizens, come first; the PDF stays among the 7 if it is really more
# relevant (forms, regulations). On a reference index, where PDFs are most of the
# passages (attachments of the pages: forms but also theses, plans, drawings), this
# raised the rank of the correct page without losing any test question. Manually
# loaded TXT and DOCX files (inbox) are "document" and are not penalised; inbox PDFs are
# "pdf" like any other and are.
PDF_PENALTY = 0.04

# Keyword → competent office map — loaded from config/offices.json
def _load_office_map() -> list[tuple[re.Pattern, str, str]]:
    import json as _j
    f = Path(__file__).parent.parent / "config" / "offices.json"
    if not f.exists():
        return []
    try:
        entries = _j.loads(f.read_text(encoding="utf-8"))
        result = []
        for e in entries:
            url = SITE_URL.rstrip("/") + e["path"] if e.get("path") else e.get("url", "")
            # Whole words, as for known facts: before, "cie" matched inside
            # "società" or "specie" and "imposta" inside "impostazioni".
            pattern = re.compile(r"\b(?:" + "|".join(re.escape(k.lower()) for k in e["keywords"]) + r")\b")
            result.append((pattern, e["name"], url))
        return result
    except (OSError, ValueError, KeyError, TypeError) as exc:
        log.warning(f"Could not load offices.json: {exc}")
        return []

_OFFICE_MAP = _load_office_map()


def suggest_office(query: str) -> str | None:
    """Suggests the competent office based on the query keywords."""
    query_lower = query.lower()
    for pattern, office_name, office_url in _OFFICE_MAP:
        if pattern.search(query_lower):
            return f"Per questo argomento puoi contattare: **{office_name}** — {office_url}"
    return None

# Synonyms for query expansion — loaded from config/synonyms.json.
# The file is specific to the domain of the organisation using the system.
# If the file does not exist, no expansion is applied.
def _load_synonyms() -> dict[str, list[str]]:
    import json as _j
    f = Path(__file__).parent.parent / "config" / "synonyms.json"
    if not f.exists():
        return {}
    try:
        return _j.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning(f"Could not load synonyms.json: {exc}")
        return {}

_SYNONYMS = _load_synonyms()


def expand_query(query: str) -> str:
    """
    Expands the query with municipal-domain synonyms.
    Returns the original query + related terms.
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
    Lightweight re-ranking without an additional model:
    - Boost if query terms appear in the title
    - Boost if the category is "servizio" (more useful to the user)
    - Penalty for PDF passages (PDF_PENALTY)
    - Penalty for duplicate chunks (same source + similar text)
    """
    query_terms = set(re.findall(r"\w+", query.lower()))

    seen_sources: dict[str, int] = {}
    scored = []

    for chunk in chunks:
        score = chunk["score"]

        # Title boost
        title_terms = set(re.findall(r"\w+", chunk.get("title", "").lower()))
        title_overlap = len(query_terms & title_terms)
        score += title_overlap * 0.02

        # Service category boost
        if chunk.get("category") == "servizio":
            score += 0.01

        if chunk.get("doc_type") == "pdf":
            score -= PDF_PENALTY

        # Same-source duplicate penalty
        src = chunk.get("source", "")
        if src in seen_sources:
            score -= 0.05 * seen_sources[src]
        seen_sources[src] = seen_sources.get(src, 0) + 1

        scored.append((score, chunk))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored]


# Known facts always available, independent of retrieval.
# Useful for basic information that the embedding does not retrieve well
# (addresses, opening hours, institutional contacts). Loaded from config/known_facts.json.
def _load_known_facts() -> list[dict]:
    import json as _j
    f = Path(__file__).parent.parent / "config" / "known_facts.json"
    if not f.exists():
        return []
    try:
        entries = _j.loads(f.read_text(encoding="utf-8"))
        result = []
        for e in entries:
            # Explicit precedence: "source" (full URL) takes priority; otherwise
            # "source_path" relative to SITE_URL; otherwise no source.
            # (It used to be a single expression with wrong precedence: facts with
            #  only "source" ended up with an empty URL.)
            if e.get("source"):
                source = e["source"]
            elif e.get("source_path"):
                source = SITE_URL.rstrip("/") + e["source_path"]
            else:
                source = ""
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
    except (OSError, ValueError, KeyError, TypeError) as exc:
        log.warning(f"Could not load known_facts.json: {exc}")
        return []

_KNOWN_FACTS = _load_known_facts()


def _inject_known_facts(query: str) -> list[dict]:
    """Returns the known facts relevant to the query."""
    q = set(re.findall(r"\w+", query.lower()))
    results = []
    for fact in _KNOWN_FACTS:
        keywords = fact["keywords"]
        require_any = fact.get("require_any", keywords)
        if q & keywords and q & require_any:
            results.append(fact["chunk"])
    return results


def _dedup_chunks(chunks: list[dict]) -> list[dict]:
    """Removes chunks with identical text, keeping the first (highest score)."""
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
    Retrieves the most relevant chunks for the query.
    Pipeline: known facts → expand → hybrid search → dedup → filter → rerank
    """
    known = _inject_known_facts(query)

    expanded_query = expand_query(query)
    query_vec = embed_query(expanded_query)

    # Detect embedding failure (zero vector) — use pure BM25
    vec_is_zero = all(v == 0.0 for v in query_vec[:10])
    if vec_is_zero:
        log.warning("Zero embedding: falling back to pure BM25 for retrieval")
        chunks = hybrid_search(query_vec, expanded_query, top_k=top_k * 3,
                               vector_weight=0.0, bm25_weight=1.0)
        chunks = _dedup_chunks(chunks)
        # Lower threshold for pure BM25 (different scale from cosine similarity)
        chunks = [c for c in chunks if c["score"] >= 0.005]
    else:
        chunks = hybrid_search(query_vec, expanded_query, top_k=top_k * 3,
                               vector_weight=VECTOR_WEIGHT, bm25_weight=BM25_WEIGHT)
        chunks = _dedup_chunks(chunks)
        chunks = [c for c in chunks if c["score"] >= MIN_SIMILARITY]
    chunks = rerank(chunks, query)

    # Prepend the known facts, avoiding duplicates by source
    known_sources = {k["source"] for k in known}
    chunks = [c for c in chunks if c.get("source") not in known_sources]
    return (known + chunks)[:top_k]


def build_context_block(chunks: list[dict]) -> str:
    """Formats the chunks with enriched metadata for the prompt."""
    if not chunks:
        return ""
    parts = []
    for i, chunk in enumerate(chunks, 1):
        # Label enriched with metadata
        label_parts = [f"Fonte {i}: {chunk.get('display_title') or chunk['title']}"]
        if chunk.get("category"):
            label_parts.append(f"tipo={chunk['category']}")
        if chunk.get("service_status"):
            label_parts.append(f"stato={chunk['service_status']}")
        if chunk.get("date"):
            label_parts.append(f"aggiornato={chunk['date']}")
        # Omit the link for migrated domains (it would be dead): the LLM cites only
        # the title. The original URL stays in the metadata for possible remapping.
        source = chunk.get("source", "")
        if source and not is_migrated_source(source):
            label_parts.append(source)
        source_label = f"[{' — '.join(label_parts)}]"
        parts.append(f"{source_label}\n{chunk['text']}")
    return "\n\n---\n\n".join(parts)


def build_prompt(
    query: str,
    context: str,
    language: str = "it",
    history: list[dict] | None = None,
    topic_query: str | None = None,
) -> list[dict]:
    """
    Builds the messages for the LLM in chat format.
    Supports conversational history (list of {"role", "content"}).

    topic_query: standalone version of the question (see contextualize_query),
                 used only to identify the competent office; the QUESTION
                 shown to the model stays the user's original one.
    """
    system = SYSTEM_PROMPT_IT if language == "it" else SYSTEM_PROMPT_EN

    office_hint = suggest_office(topic_query or query)
    # Today's date: lets the model tell whether deadlines and calls have already passed
    today = f"(Data di oggi: {now_local():%d/%m/%Y})\n" if language == "it" else f"(Today's date: {now_local():%Y-%m-%d})\n"

    if context:
        # The competent office is provided even when context is present, as a
        # fallback: if the retrieved chunks are off-topic (e.g. a service
        # question that pulls in budget documents), the model must be able to
        # direct the user to the right office instead of answering from
        # irrelevant documents. It is conditional: if the context really
        # answers, the model ignores it.
        office_text = (
            f"\n\n(Se il CONTESTO non risponde davvero alla DOMANDA, NON "
            f"rispondere usando quei documenti: dichiara che non hai "
            f"l'informazione specifica e indica l'ufficio competente. {office_hint})"
            if office_hint else ""
        )
        user_content = (
            f"CONTESTO (estratto dalla base di conoscenza — usa SOLO queste informazioni):\n\n"
            f"{context}\n\n"
            f"---\n\n"
            f"{today}DOMANDA: {query}{office_text}"
        )
    else:
        office_text = f"\n\n{office_hint}" if office_hint else ""
        user_content = (
            f"{today}DOMANDA: {query}\n\n"
            f"(Non ho trovato nella base di conoscenza informazioni specifiche "
            f"su questo argomento. Dillo chiaramente all'utente.{office_text})"
        )

    messages = [{"role": "system", "content": system}]

    # Add conversation history (max 3 turns)
    if history:
        messages.extend(history[-6:])  # max 3 turns = 6 messages

    messages.append({"role": "user", "content": user_content})
    return messages


def detect_language(text: str) -> str:
    """
    Detects the query language (simple heuristic).
    Returns 'it' or 'en'.
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
# Contextualising the question for retrieval
# ---------------------------------------------------------------------------
#
# Retrieval works only on the current question's text: in a dialogue, a
# follow-up such as "ma quali sono i requisiti per avere il beneficio?" no
# longer contains the topic ("sconto autobus") and pulls in off-topic documents,
# even though the model receives the history. Here the question is rewritten in standalone
# form (only when there is history) and used for search, known facts and the competent
# office; the model still gets the original question + history.

_REWRITE_SYSTEM = (
    "Riscrivi l'ultimo messaggio dell'utente come una domanda autonoma, "
    "comprensibile senza leggere la conversazione precedente, nella stessa "
    "lingua del messaggio. Esplicita l'argomento a cui si riferiscono pronomi "
    "e riferimenti impliciti (per esempio 'il beneficio', 'lo sconto', "
    "'questo servizio', 'quell'ufficio') usando le parole della conversazione. "
    "Non rispondere, non aggiungere informazioni, non cambiare il senso. "
    "Non aggiungere nomi di luoghi, città o enti che l'utente non ha scritto "
    "(la conversazione riguarda già il sito su cui si trova l'assistente). "
    "Se il messaggio è già autonomo o introduce un argomento nuovo, "
    "restituiscilo invariato. Rispondi SOLO con la domanda riscritta, "
    "su una riga, senza virgolette né commenti."
)
_REWRITE_MAX_TURN_CHARS = 600   # characters of each history turn included in the prompt
_REWRITE_MAX_LEN = 400          # beyond this length the rewrite is suspect


def _fallback_contextualize(query: str, history: list[dict]) -> str:
    """Model-free contextualisation: appends the user's last question.

    Less precise than the rewrite, but brings the keywords
    of the previous turn into retrieval when the model is unreachable."""
    prev = next(
        (m.get("content", "") for m in reversed(history) if m.get("role") == "user"),
        "",
    )
    prev = re.sub(r"\s+", " ", prev).strip()
    return f"{query} {prev}".strip() if prev else query


def _rewrite_prompt(query: str, history: list[dict]) -> list[dict]:
    lines = []
    for m in history[-6:]:
        who = "Utente" if m.get("role") == "user" else "Assistente"
        text = re.sub(r"\s+", " ", m.get("content", "")).strip()
        if len(text) > _REWRITE_MAX_TURN_CHARS:
            text = text[:_REWRITE_MAX_TURN_CHARS] + "…"
        lines.append(f"{who}: {text}")
    user_content = (
        "Conversazione precedente:\n" + "\n".join(lines)
        + f"\n\nUltimo messaggio dell'utente da riscrivere: {query}"
    )
    return [
        {"role": "system", "content": _REWRITE_SYSTEM},
        {"role": "user", "content": user_content},
    ]


async def contextualize_query(
    query: str,
    history: list[dict] | None,
    uid: str | None = None,
) -> str:
    """Returns the question in standalone form for retrieval.

    Without history (or with QUERY_REWRITE off) returns the question
    unchanged. On a model error it uses the deterministic fallback:
    the function must never make the answer fail."""
    if not history:
        return query
    if not QUERY_REWRITE:
        return _fallback_contextualize(query, history)
    try:
        messages = _rewrite_prompt(query, history)
        if _USES_CLAUDE:
            text = await _rewrite_claude(messages, uid=uid)
        else:
            text = await generate_ollama(messages)
        text = text.strip().splitlines()[0].strip().strip('"«»\'') if text.strip() else ""
        if not text or len(text) > _REWRITE_MAX_LEN:
            return _fallback_contextualize(query, history)
        return text
    except Exception:
        log.warning("Question rewrite failed: using the fallback", exc_info=True)
        return _fallback_contextualize(query, history)


async def _rewrite_claude(messages: list[dict], uid: str | None = None) -> str:
    """Short, non-streaming call for rewriting the question."""
    system_content = next(
        (m["content"] for m in messages if m["role"] == "system"), ""
    )
    user_messages = [m for m in messages if m["role"] != "system"]
    response = await _claude_client().with_options(timeout=20.0).messages.create(
        model=_REWRITE_MODEL,
        system=system_content,
        messages=user_messages,
        **_model_options(_REWRITE_MODEL, temperature=0.0, max_tokens=200),
    )
    try:
        if response.usage:
            _record_tokens(response.usage.input_tokens, response.usage.output_tokens,
                           _REWRITE_MODEL, uid=uid)
    except (AttributeError, TypeError, ValueError) as exc:
        log.debug("Token count not recorded: %s", exc)
    return _text_of(response)


# ---------------------------------------------------------------------------
# Generation with Ollama
# ---------------------------------------------------------------------------

async def generate_ollama(messages: list[dict]) -> str:
    """Calls Ollama in non-streaming mode and returns the complete answer."""
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
    """Calls Ollama in streaming mode, yielding tokens progressively. Retries once on 500."""
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
            async with (
                httpx.AsyncClient(timeout=600.0) as client,
                client.stream("POST", f"{OLLAMA_BASE_URL}/api/chat", json=payload) as resp,
            ):
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
            log.warning(f"Ollama HTTP {e.response.status_code} (attempt {attempt+1}/2)")
            continue
        except Exception:
            raise
    raise last_exc


# ---------------------------------------------------------------------------
# Generation with the Claude API
# ---------------------------------------------------------------------------

async def generate_claude(messages: list[dict], uid: str | None = None) -> str:
    """Calls Claude (direct API or Bedrock) and returns the complete answer.

    uid: opaque id of the user who sent the question (for the cost history)."""
    system_content = next(
        (m["content"] for m in messages if m["role"] == "system"), ""
    )
    user_messages = [m for m in messages if m["role"] != "system"]

    response = await _claude_client().messages.create(
        model=_ANSWER_MODEL,
        system=system_content,
        messages=user_messages,
        **_model_options(_ANSWER_MODEL, temperature=0.3, max_tokens=1024),
    )
    try:
        if response.usage:
            _record_tokens(response.usage.input_tokens, response.usage.output_tokens,
                           _ANSWER_MODEL, uid=uid)
    except (AttributeError, TypeError, ValueError) as exc:
        log.debug("Token count not recorded: %s", exc)
    text = _text_of(response)
    if response.stop_reason == "refusal":
        log.warning("The model refused the request")
        text = (text + "\n\n" + _REFUSAL_TEXT).strip()
    return text


async def stream_claude(messages: list[dict], uid: str | None = None) -> AsyncGenerator[str, None]:
    """Calls Claude (direct API or Bedrock) in streaming mode.

    uid: opaque id of the user who sent the question (for the cost history)."""
    system_content = next(
        (m["content"] for m in messages if m["role"] == "system"), ""
    )
    user_messages = [m for m in messages if m["role"] != "system"]

    async with _claude_client().messages.stream(
        model=_ANSWER_MODEL,
        system=system_content,
        messages=user_messages,
        **_model_options(_ANSWER_MODEL, temperature=0.3, max_tokens=1024),
    ) as stream:
        sent = False
        async for text in stream.text_stream:
            sent = sent or bool(text)
            yield text
        try:
            final = await stream.get_final_message()
        except (AttributeError, TypeError, ValueError) as exc:
            log.debug("Final message not available: %s", exc)
            final = None
        if final and final.stop_reason == "refusal":
            log.warning("The model refused the request")
            yield ("\n\n" if sent else "") + _REFUSAL_TEXT
        try:
            if final and final.usage:
                _record_tokens(final.usage.input_tokens, final.usage.output_tokens,
                               _ANSWER_MODEL, uid=uid)
        except (AttributeError, TypeError, ValueError) as exc:
            log.debug("Token count not recorded: %s", exc)


# ---------------------------------------------------------------------------
# Answer cache (in-memory, simple LRU)
# ---------------------------------------------------------------------------

import hashlib as _hashlib
import json as _json
import time as _time
from collections import Counter as _Counter
from collections import OrderedDict
from datetime import timedelta as _timedelta
from pathlib import Path as _Path

# Log of unanswered queries to identify content gaps
_GAPS_LOG = _Path(__file__).parent.parent / "data" / "gaps.jsonl"


def _log_gap(query: str, chunks_found: int, weak: bool = False, rid: str | None = None):
    """Records queries with 0 chunks or weak retrieval (without personal data).

    weak=True means chunks were found but with low relevance (below
    RETRIEVAL_CONFIDENCE): cases that escape the 0-chunk log but often
    produce poor answers. rid: question identifier (see
    api/datalog.py), used to delete it on request.
    """
    try:
        _GAPS_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": now_local().isoformat(timespec="seconds"),
            "query": query[:200],
            "chunks": chunks_found,
            "weak": weak,
        }
        if rid:
            entry["rid"] = rid
        with file_lock(_GAPS_LOG), open(_GAPS_LOG, "a", encoding="utf-8") as f:
            f.write(_json.dumps(entry, ensure_ascii=False) + "\n")
    except (OSError, TypeError, ValueError) as exc:
        log.debug("Gap log not written: %s", exc)

_CACHE_MAX = 200  # max cached answers
_response_cache: OrderedDict = OrderedDict()
_retrieval_slots = asyncio.Semaphore(4)   # concurrent retrievals (embedding requests to Ollama)

_STATS_FILE = _Path(__file__).parent.parent / "data" / "stats.json"

_query_count: int = 0
_response_times: list[float] = []
_query_freq: _Counter = _Counter()
_query_seen: dict[str, float] = {}   # question -> when it was last asked (for RETENTION_STATS_DAYS)
_hour_counts: list[int] = [0] * 24

# Token tracking (Claude provider only)
_token_in_total: int = 0
_token_out_total: int = 0
_token_cost_total: float = 0.0
_token_history: list[dict] = []   # last 100 records
_TOKEN_HISTORY_MAX = 100

# USD prices per million tokens (input, output), Anthropic API price list
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-7":   (5.00, 25.00),
    "claude-sonnet-5":    (2.00, 10.00),   # also claude-sonnet-5-5
    "claude-sonnet-4-6":  (3.00, 15.00),
    "claude-haiku-4-5":   (1.00,  5.00),
}
# On Bedrock the geographic profiles (eu., us., ...) cost about 10% more
_BEDROCK_GEO_PREFIXES = ("eu.", "us.", "jp.", "apac.", "au.", "ca.")
_BEDROCK_GEO_MARKUP = 1.10


def _cost_usd(in_tok: int, out_tok: int, model: str) -> float:
    # Bedrock id: "eu.anthropic.claude-sonnet-4-6" → "claude-sonnet-4-6" (estimate)
    markup = _BEDROCK_GEO_MARKUP if model.startswith(_BEDROCK_GEO_PREFIXES) else 1.0
    base = model.split("anthropic.", 1)[1] if "anthropic." in model else model
    for prefix, (p_in, p_out) in _MODEL_PRICING.items():
        if base.startswith(prefix):
            return (in_tok * p_in + out_tok * p_out) * markup / 1_000_000
    return 0.0


def _record_tokens(in_tok: int, out_tok: int, model: str,
                   uid: str | None = None) -> None:
    """Records tokens and cost of a model call.

    uid: opaque user id (as in usage.jsonl). The name stays in
    users.json and is resolved only in the dashboard (admin view)."""
    global _token_in_total, _token_out_total, _token_cost_total
    cost = _cost_usd(in_tok, out_tok, model)
    _token_in_total  += in_tok
    _token_out_total += out_tok
    _token_cost_total += cost
    entry = {
        "ts":    now_local().isoformat(timespec="seconds"),
        "in":    in_tok,
        "out":   out_tok,
        "model": model,
        "cost":  round(cost, 6),
    }
    if uid:
        entry["uid"] = uid
    _token_history.append(entry)
    if len(_token_history) > _TOKEN_HISTORY_MAX:
        _token_history.pop(0)
    _save_stats()  # persist immediately: for streaming the tokens arrive after answer()'s _save_stats()


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
        now = _time.time()   # questions saved before the dates existed start their retention now
        _query_seen.update({k: d.get("query_seen", {}).get(k, now) for k in _query_freq})
        hc = d.get("hour_counts", [0] * 24)
        _hour_counts    = (hc + [0] * 24)[:24]
        _token_in_total   = d.get("token_in_total", 0)
        _token_out_total  = d.get("token_out_total", 0)
        _token_cost_total = d.get("token_cost_total", 0.0)
        _token_history    = d.get("token_history", [])[-_TOKEN_HISTORY_MAX:]
    except (OSError, ValueError, TypeError, AttributeError) as e:
        log.warning(f"Could not load stats: {e}")
        return
    _expire_stats()


def expire_and_save_stats() -> None:
    """Applies RETENTION_STATS_DAYS and writes stats.json: called at startup and every few
    hours, so the data expire also when nobody is asking questions."""
    _expire_stats()
    _save_stats()


def _expire_stats():
    """Drops the questions and the per-user token records older than RETENTION_STATS_DAYS
    (stats.json keeps the text of the most frequent questions and the user ids)."""
    if RETENTION_STATS_DAYS <= 0:
        return
    limit = _time.time() - RETENTION_STATS_DAYS * 86400
    for key in [k for k in _query_freq if _query_seen.get(k, limit + 1) < limit]:
        del _query_freq[key]
        _query_seen.pop(key, None)
    cutoff = (now_local() - _timedelta(days=RETENTION_STATS_DAYS)).isoformat(timespec="seconds")
    _token_history[:] = [e for e in _token_history if str(e.get("ts", "")) >= cutoff]


def _save_stats():
    _expire_stats()
    try:
        _STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(_STATS_FILE, _json.dumps({
                "query_count":      _query_count,
                "response_times":   _response_times[-100:],
                "query_freq":       dict(_query_freq.most_common(100)),
                "query_seen":       {k: _query_seen[k] for k in dict(_query_freq.most_common(100)) if k in _query_seen},
                "hour_counts":      _hour_counts,
                "token_in_total":   _token_in_total,
                "token_out_total":  _token_out_total,
                "token_cost_total": _token_cost_total,
                "token_history":    _token_history[-_TOKEN_HISTORY_MAX:],
            }))
    except (OSError, TypeError, ValueError) as e:
        log.warning(f"Could not save stats: {e}")


_load_stats()


def get_query_count() -> int:
    return _query_count


def get_activity_stats() -> dict:
    _expire_stats()
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


def _freq_key(query: str) -> str:
    """Form of the question in the most-frequent counter (stats.json)."""
    return re.sub(r"\s+", " ", query.strip().lower().strip("?! "))


def forget_queries(queries: list[str]) -> int:
    """Removes from the process memory the questions deleted on request
    (POST /usage/forget): the most-frequent-questions counter, which ends up in
    stats.json, and the answer cache. Returns how many counter entries were
    touched."""
    touched = 0
    for q in queries:
        if not (q or "").strip():
            continue
        key = _freq_key(q)
        if _query_freq.get(key, 0) > 0:
            _query_freq[key] -= 1
            if _query_freq[key] <= 0:
                del _query_freq[key]
                _query_seen.pop(key, None)
            touched += 1
        _response_cache.pop(_cache_key(q.strip()), None)
    if touched:
        _save_stats()
    return touched


def forget_usage_of(uids: set[str]) -> int:
    """Removes the usage records of these users (token_history in stats.json), when all their
    questions are deleted. Records of other users are kept. Returns how many were removed."""
    before = len(_token_history)
    _token_history[:] = [e for e in _token_history if e.get("uid") not in uids]
    removed = before - len(_token_history)
    if removed:
        _save_stats()
    return removed


def clear_response_cache() -> None:
    """Forgets the cached answers: they may cite documents the new index no longer has."""
    _response_cache.clear()


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
# Public interface
# ---------------------------------------------------------------------------

async def answer(
    query: str,
    stream: bool = False,
    history: list[dict] | None = None,
    uid: str | None = None,
    rid: str | None = None,
):
    """
    Answers a question using RAG.

    If stream=False: returns a dict with the answer and sources.
    If stream=True: returns (AsyncGenerator[str], sources, answer language).

    history: list of {"role": "user"|"assistant", "content": str}
             to keep the conversational context (max 3 turns).
    uid:     opaque id of the authenticated user, recorded in the token/cost
             history (admin dashboard).
    rid:     question identifier (see api/datalog.py). The API log
             reports only this, never the question text.
    """
    global _query_count
    query = query.strip()
    if not query:
        raise ValueError("Query vuota")
    _query_count += 1
    _hour_counts[now_local().hour] += 1
    log_rid = rid or "-"

    # Greetings, thanks, compliments and insults: fixed answer, without
    # search or model, kept out of the gaps and the most frequent questions.
    small = smalltalk.classify(query)
    if small:
        kind, language = small
        log.info("Question %s: message without a question (%s), fixed reply", log_rid, kind)
        text = smalltalk.reply(kind, language)
        _save_stats()
        if stream:
            async def _fixed_gen():
                yield text
            return _fixed_gen(), [], language
        return {"answer": text, "sources": [], "language": language}

    _query_freq[_freq_key(query)] += 1
    _query_seen[_freq_key(query)] = _time.time()

    # Cache only for queries without history (stateless conversations)
    use_cache = not history and not stream
    if use_cache:
        cached = _cache_get(query)
        if cached:
            log.info("Question %s: answer served from cache", log_rid)
            return cached

    language = detect_language(query)
    # In turns after the first, retrieval uses the standalone question
    # (the dialogue topic), not just the follow-up text.
    retrieval_query = await contextualize_query(query, history, uid=uid)
    if retrieval_query != query:
        log.info("Question %s rewritten for retrieval (%d -> %d characters)",
                 log_rid, len(query), len(retrieval_query))
    # The query embedding is a blocking call to Ollama: a worker thread, at most a few at a time
    async with _retrieval_slots:
        chunks = await asyncio.to_thread(retrieve_context, retrieval_query)
    context = build_context_block(chunks)
    messages = build_prompt(query, context, language, history=history,
                            topic_query=retrieval_query)

    # Hide the source URL on migrated domains: the link would be dead, so
    # the source is shown by title only (empty url).
    sources = [
        {
            "title": c.get("display_title") or c["title"],
            "url": "" if is_migrated_source(c.get("source", "")) else c.get("source", ""),
            "score": c["score"],
        }
        for c in chunks
    ]
    seen = set()
    unique_sources = []
    for s in sources:
        # Dedup by URL when present; by title when the link is hidden —
        # "title-only" documents have an empty url and must not be collapsed into one.
        # The same file with and without www. (or http/https) counts once.
        key = (re.sub(r"^https?://(?:www\.)?", "", s["url"]).rstrip("/")
               if s["url"] else f"title::{s['title']}")
        if key not in seen:
            seen.add(key)
            unique_sources.append(s)

    # Only the identifier: the text stays in the data/ files, which have an
    # expiry and can be cleaned on request (the API log cannot).
    log.info("Question %s | %d characters | lang=%s | chunks=%d | provider=%s",
             log_rid, len(query), language, len(chunks), LLM_PROVIDER)

    if len(chunks) == 0:
        await asyncio.to_thread(_log_gap, retrieval_query, 0, rid=rid)
    else:
        # "Weak" retrieval: chunks found but even the best is below the
        # confidence threshold (often documents that are only thematically close).
        # Track them to surface content gaps that would otherwise be invisible.
        top_score = max(c.get("score", 0.0) for c in chunks)
        if top_score < RETRIEVAL_CONFIDENCE:
            await asyncio.to_thread(_log_gap, retrieval_query, len(chunks), weak=True, rid=rid)

    if stream:
        if not chunks:
            # No context found — bypass the LLM with a reliable fallback
            office_hint = suggest_office(retrieval_query)
            fallback = (
                "Non ho trovato informazioni specifiche su questo argomento "
                "nella base di conoscenza."
            )
            if office_hint:
                fallback += f"\n\n{office_hint}"

            async def _no_context_gen():
                yield fallback

            _save_stats()
            return _no_context_gen(), unique_sources, language

        if _USES_CLAUDE:
            gen = stream_claude(messages, uid=uid)
        else:
            gen = stream_ollama(messages)
        _save_stats()
        return gen, unique_sources, language

    else:
        _t0 = _time.monotonic()
        if _USES_CLAUDE:
            text = await generate_claude(messages, uid=uid)
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
