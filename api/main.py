"""
Backend FastAPI di Zautte.

Endpoints:
  POST /chat          → risposta completa (JSON)
  POST /chat/stream   → risposta in streaming (Server-Sent Events)
  GET  /health        → stato del servizio
  GET  /stats         → statistiche vector store
"""

import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Security
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import API_CORS_ORIGINS, SITE_NAME, LLM_PROVIDER, OLLAMA_MODEL, CLAUDE_MODEL, ADMIN_API_KEY
from api.rag import answer, get_query_count, get_activity_stats
from indexer.vector_store import get_stats, EMBEDDINGS_FILE, is_bm25_active, get_top_doc

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

limiter = Limiter(key_func=get_remote_address)

import datetime as _dt_module
_startup_time = _dt_module.datetime.now()

# --- Autenticazione endpoint admin ---
_api_key_header = APIKeyHeader(name="X-Admin-Key", auto_error=False)


async def require_admin(key: str | None = Security(_api_key_header)):
    """Dependency per proteggere gli endpoint admin con API key."""
    if not ADMIN_API_KEY:
        return  # autenticazione disabilitata (sviluppo)
    if key != ADMIN_API_KEY:
        raise HTTPException(status_code=403, detail="Accesso non autorizzato")


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    import signal

    log.info("Avvio chatbot...")
    try:
        from indexer.embedder import check_ollama_embed
        if check_ollama_embed():
            stats = get_stats()
            log.info(f"Pronto. Ollama raggiungibile. Vector store: {stats['total_chunks']} chunk.")
        else:
            log.warning("Ollama non raggiungibile all'avvio. Assicurarsi che 'ollama serve' sia attivo.")
    except Exception as e:
        log.error(f"Errore durante l'avvio: {e}")

    # Graceful shutdown: attende il completamento delle richieste in corso
    shutdown_event = asyncio.Event()

    def _handle_sigterm(*_):
        log.info("SIGTERM ricevuto — attendo completamento richieste in corso...")
        shutdown_event.set()

    signal.signal(signal.SIGTERM, _handle_sigterm)

    yield

    if shutdown_event.is_set():
        await asyncio.sleep(2)  # finestra per completare richieste streaming

    log.info("Chatbot arrestato.")


app = FastAPI(
    title=f"Zautte – {SITE_NAME}" if SITE_NAME else "Zautte",
    description="API di Zautte, assistente virtuale RAG.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",       # disabilita in produzione se non necessario
    redoc_url=None,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Serve il widget JS come file statico (GET /widget/chatbot-widget.js)
_widget_dir = Path(__file__).parent.parent / "widget"
if _widget_dir.exists():
    app.mount("/widget", StaticFiles(directory=str(_widget_dir)), name="widget")

app.add_middleware(
    CORSMiddleware,
    allow_origins=API_CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["Content-Type"],
)


# ---------------------------------------------------------------------------
# Modelli request/response
# ---------------------------------------------------------------------------

class FeedbackRequest(BaseModel):
    question: str = Field(..., max_length=1000)
    answer: str = Field(..., max_length=5000)
    rating: int = Field(..., ge=-1, le=1, description="-1=negativo, 1=positivo")


class HistoryMessage(BaseModel):
    role: str = Field(..., pattern="^(user|assistant)$")
    content: str = Field(..., max_length=2000)


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=1000,
                          description="La domanda del visitatore")
    history: list[HistoryMessage] = Field(
        default=[],
        max_length=6,
        description="Ultimi 3 turni di conversazione (opzionale)"
    )


class Source(BaseModel):
    title: str
    url: str
    score: float


class ChatResponse(BaseModel):
    answer: str
    sources: list[Source]
    language: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

def _read_gaps_count() -> int:
    from pathlib import Path as _P
    f = _P(__file__).parent.parent / "data" / "gaps.jsonl"
    if not f.exists():
        return 0
    try:
        return sum(1 for line in f.open(encoding="utf-8") if line.strip())
    except Exception:
        return 0


def _read_recent_gaps(n: int) -> list[dict]:
    from pathlib import Path as _P
    import json as _j
    f = _P(__file__).parent.parent / "data" / "gaps.jsonl"
    if not f.exists():
        return []
    lines = []
    try:
        lines = [line for line in f.open(encoding="utf-8") if line.strip()]
    except Exception:
        return []
    recent = []
    for line in reversed(lines):
        try:
            e = _j.loads(line)
            recent.append({"ts": e.get("ts", ""), "query": e["query"]})
        except Exception:
            pass
        if len(recent) >= n:
            break
    return list(reversed(recent))


def _read_feedback_summary() -> dict:
    from pathlib import Path as _P
    import json as _j
    f = _P(__file__).parent.parent / "data" / "feedback.jsonl"
    if not f.exists():
        return {"total": 0, "positive": 0, "negative": 0}
    pos = neg = 0
    try:
        for line in f.open(encoding="utf-8"):
            try:
                e = _j.loads(line)
                if e.get("rating") == 1:
                    pos += 1
                elif e.get("rating") == -1:
                    neg += 1
            except Exception:
                pass
    except Exception:
        pass
    return {"total": pos + neg, "positive": pos, "negative": neg}


@app.get("/health")
async def health():
    """Verifica che il servizio sia attivo."""
    import datetime as _dt
    stats = get_stats()
    llm_info = OLLAMA_MODEL if LLM_PROVIDER == "ollama" else CLAUDE_MODEL

    last_indexed = None
    if EMBEDDINGS_FILE.exists():
        mtime = EMBEDDINGS_FILE.stat().st_mtime
        last_indexed = _dt.datetime.fromtimestamp(mtime).strftime("%d/%m/%Y %H:%M")

    return {
        "status": "ok",
        "indexed_chunks": stats["total_chunks"],
        "unique_sources": stats["unique_sources"],
        "doc_types": stats["doc_types"],
        "llm_provider": LLM_PROVIDER,
        "llm_model": llm_info,
        "hybrid_search": is_bm25_active(),
        "last_indexed": last_indexed,
        "queries_since_restart": get_query_count(),
        "gaps_total": _read_gaps_count(),
        "gaps_recent": _read_recent_gaps(5),
        "feedback": _read_feedback_summary(),
        "activity": get_activity_stats(),
        "top_doc": get_top_doc(),
        "uptime_seconds": int((_dt_module.datetime.now() - _startup_time).total_seconds()),
    }


@app.get("/stats")
async def stats(_: None = Security(require_admin)):
    """Statistiche sul contenuto indicizzato."""
    return get_stats()


@app.get("/gaps")
async def gaps(limit: int = 50, _: None = Security(require_admin)):
    """Query senza risposta — utile per identificare gap di contenuto."""
    import json as _json
    from pathlib import Path as _Path
    gaps_file = _Path(__file__).parent.parent / "data" / "gaps.jsonl"
    if not gaps_file.exists():
        return {"gaps": [], "total": 0}
    entries = []
    with open(gaps_file, encoding="utf-8") as f:
        for line in f:
            try:
                entries.append(_json.loads(line))
            except Exception:
                pass
    return {"gaps": entries[-limit:], "total": len(entries)}


@app.post("/feedback")
@limiter.limit("60/hour")
async def feedback(request: Request, req: FeedbackRequest):
    """Salva il feedback dell'utente (pollice su/giù) senza dati personali."""
    import json as _json
    import datetime as _dt
    from pathlib import Path as _Path
    feedback_file = _Path(__file__).parent.parent / "data" / "feedback.jsonl"
    feedback_file.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": _dt.datetime.now().isoformat(timespec="seconds"),
        "rating": req.rating,
        "question": req.question[:200],
        "answer_preview": req.answer[:100],
    }
    with open(feedback_file, "a", encoding="utf-8") as f:
        f.write(_json.dumps(entry, ensure_ascii=False) + "\n")
    return {"ok": True}


@app.get("/feedback/negative")
async def feedback_negative(limit: int = 200):
    """Domande con feedback negativo — solo question + ts, nessun dato personale."""
    import json as _json
    from pathlib import Path as _Path
    feedback_file = _Path(__file__).parent.parent / "data" / "feedback.jsonl"
    if not feedback_file.exists():
        return {"items": [], "total_negative": 0, "total": 0}
    entries = []
    total = 0
    with open(feedback_file, encoding="utf-8") as f:
        for line in f:
            try:
                e = _json.loads(line)
                total += 1
                if e.get("rating") == -1:
                    entries.append({"question": e.get("question", ""), "ts": e.get("ts", "")})
            except Exception:
                pass
    return {"items": entries[-limit:], "total_negative": len(entries), "total": total}


@app.get("/feedback/list")
async def feedback_list(limit: int = 100, _: None = Security(require_admin)):
    """Lista feedback ricevuti — solo admin."""
    import json as _json
    from pathlib import Path as _Path
    feedback_file = _Path(__file__).parent.parent / "data" / "feedback.jsonl"
    if not feedback_file.exists():
        return {"feedback": [], "total": 0, "positive": 0, "negative": 0}
    entries = []
    with open(feedback_file, encoding="utf-8") as f:
        for line in f:
            try:
                entries.append(_json.loads(line))
            except Exception:
                pass
    positive = sum(1 for e in entries if e.get("rating") == 1)
    negative = sum(1 for e in entries if e.get("rating") == -1)
    return {
        "feedback": entries[-limit:],
        "total": len(entries),
        "positive": positive,
        "negative": negative,
    }


@app.get("/crawl-history")
async def crawl_history():
    """Storico crawling e indicizzazione (ultimi eventi dal sync log)."""
    import re
    from pathlib import Path as _P

    log_path = _P("/var/log/chatbot-sync.log")
    if not log_path.exists():
        return {"events": [], "current_html": None, "current_pdf": None}

    # Lettura riga-per-riga: efficiente in memoria su file da 50MB,
    # filtra in Python senza dipendere da subprocess/grep
    MARKERS = ("Vector store caricato:", "Crawl incrementale completato",
               "=== Crawl", "[HTML ", "[PDF ")

    try:
        with open(log_path, encoding="utf-8", errors="replace") as fh:
            lines = [ln.rstrip("\n") for ln in fh if any(m in ln for m in MARKERS)]
    except Exception:
        lines = []

    ts_re   = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}):\d{2},\d+ \[INFO\] (.+)$")
    html_re = re.compile(r"\[HTML (\d+)/(\d+)\]")
    pdf_re  = re.compile(r"\[PDF (\d+)/(\d+)\]")

    events: list[dict] = []
    last_html: dict | None = None
    last_pdf:  dict | None = None
    last_ts = ""

    for line in lines:
        m = ts_re.match(line)
        if m:
            last_ts = m.group(1)
            msg = m.group(2).strip()
        else:
            msg = line.strip()   # righe senza timestamp (es. "=== Crawl ... ===")

        if "Crawl incrementale completato" in msg or "=== Crawl" in msg:
            events.append({"ts": last_ts, "type": "crawl_end",
                           "label": "Crawl HTML completato"})
        elif "Vector store caricato:" in msg:
            m2 = re.search(r"(\d+) chunk", msg)
            if m2:
                n = int(m2.group(1))
                events.append({"ts": last_ts, "type": "vs_loaded", "chunks": n,
                               "label": f"Vector store: {n:,} chunk"})
        else:
            mh = html_re.search(msg)
            if mh:
                cur, tot = int(mh.group(1)), int(mh.group(2))
                last_html = {"ts": last_ts, "current": cur, "total": tot}
                if cur == tot:
                    events.append({"ts": last_ts, "type": "html_done",
                                   "label": f"HTML: {tot:,} pagine completate",
                                   "pages": tot})
                continue
            mp = pdf_re.search(msg)
            if mp:
                cur, tot = int(mp.group(1)), int(mp.group(2))
                last_pdf = {"ts": last_ts, "current": cur, "total": tot}

    # Deduplica eventi VS (mantieni solo i cambi di chunk count)
    seen_chunks: set[int] = set()
    deduped: list[dict] = []
    for e in events:
        if e["type"] == "vs_loaded":
            if e["chunks"] in seen_chunks:
                continue
            seen_chunks.add(e["chunks"])
        deduped.append(e)

    from fastapi.responses import JSONResponse as _JR
    return _JR(
        content={"events": deduped[-20:], "current_html": last_html, "current_pdf": last_pdf},
        headers={"Cache-Control": "no-store"},
    )


@app.post("/chat", response_model=ChatResponse)
@limiter.limit("20/hour")
async def chat(request: Request, req: ChatRequest):
    """
    Risposta completa alla domanda del visitatore.
    Attende la risposta intera prima di ritornare.
    """
    try:
        history = [m.model_dump() for m in req.history] if req.history else None
        result = await answer(req.question, stream=False, history=history)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.error(f"Errore in /chat: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Errore interno del server")


@app.post("/chat/stream")
@limiter.limit("20/hour")
async def chat_stream(request: Request, req: ChatRequest):
    """
    Risposta in streaming via Server-Sent Events (SSE).
    Il widget riceve i token man mano che vengono generati.

    Formato SSE:
      data: {"token": "..."}\n\n        → token di testo
      data: {"sources": [...]}\n\n      → fonti (ultimo messaggio)
      data: {"done": true}\n\n          → fine stream
    """
    try:
        history = [m.model_dump() for m in req.history] if req.history else None
        generator, sources = await answer(req.question, stream=True, history=history)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.error(f"Errore in /chat/stream: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Errore interno del server")

    async def event_stream():
        import asyncio as _asyncio

        queue: _asyncio.Queue = _asyncio.Queue()

        async def _produce():
            try:
                async for token in generator:
                    await queue.put(("token", token))
                await queue.put(("sources", None))
                await queue.put(("done", None))
            except Exception as exc:
                await queue.put(("error", str(exc)))

        task = _asyncio.create_task(_produce())
        try:
            while True:
                try:
                    kind, value = await _asyncio.wait_for(queue.get(), timeout=15.0)
                except _asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue

                if kind == "token":
                    yield f"data: {json.dumps({'token': value}, ensure_ascii=False)}\n\n"
                elif kind == "sources":
                    sources_data = json.dumps(
                        {"sources": [s.__dict__ if hasattr(s, '__dict__') else s
                                     for s in sources]},
                        ensure_ascii=False,
                    )
                    yield f"data: {sources_data}\n\n"
                    yield 'data: {"done": true}\n\n'
                    break
                elif kind == "done":
                    yield 'data: {"done": true}\n\n'
                    break
                elif kind == "error":
                    log.error(f"Errore durante lo streaming: {value}")
                    yield 'data: {"error": "Errore durante la generazione"}\n\n'
                    yield 'data: {"done": true}\n\n'
                    break
        finally:
            task.cancel()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # importante per nginx
        },
    )
