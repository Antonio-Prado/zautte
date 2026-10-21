"""
Zautte FastAPI backend.

Endpoints:
  POST /chat          → complete answer (JSON)
  POST /chat/stream   → streaming answer (Server-Sent Events)
  GET  /health        → service status
  GET  /stats         → vector store statistics
  POST /usage/forget  → deletion of questions on request (admin)
"""

import asyncio
import datetime
import json
import logging
import re
import secrets
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Security
from fastapi.exception_handlers import http_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException as StarletteHTTPException

sys.path.insert(0, str(Path(__file__).parent.parent))

from api import auth, conversations, llm_gate, quota
from api.admin_guard import check_admin_key
from api.auth import require_user
from api.datalog import forget
from api.filelock import file_lock
from api.limiter import limiter
from api.pii import BLOCK_MESSAGE, contains_personal, redact
from api.rag import (
    answer,
    expire_and_save_stats,
    forget_queries,
    forget_usage_of,
    get_activity_stats,
    get_query_count,
)
from api.safefile import write_text_atomic
from config.settings import (
    ADMIN_API_KEY,
    ADMIN_PATH,
    API_CORS_ORIGINS,
    API_DOCS,
    AUTH_ENABLED,
    BEDROCK_MODEL,
    CLAUDE_MODEL,
    DAILY_QUESTION_LIMIT,
    INSTANCE_DIR,
    LLM_PROVIDER,
    OLLAMA_MODEL,
    SITE_NAME,
    now_local,
)
from indexer.vector_store import EMBEDDINGS_FILE, get_stats, get_top_doc, is_bm25_active

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

_startup_time = now_local()
_DATA_DIR = Path(__file__).parent.parent / "data"

# --- Admin endpoint authentication ---
_api_key_header = APIKeyHeader(name="X-Admin-Key", auto_error=False)


async def require_admin(request: Request, key: str | None = Security(_api_key_header)):
    """Dependency protecting the admin endpoints with an API key (with a limit on
    wrong attempts, see api/admin_guard.py)."""
    if not check_admin_key(request, key):
        raise HTTPException(status_code=403, detail="Accesso non autorizzato")


@asynccontextmanager
async def lifespan(app: FastAPI):
    import signal

    from api.auth import auth_secret_ok
    if AUTH_ENABLED and not auth_secret_ok():
        raise RuntimeError("AUTH_ENABLED=true needs AUTH_SECRET of at least 16 characters: refusing to start")
    from api.admin_guard import admin_key_ok
    if ADMIN_API_KEY and not admin_key_ok():
        raise RuntimeError("ADMIN_API_KEY is a placeholder or shorter than 16 characters: refusing to start "
                           "(generate one, e.g. python -c 'import secrets; print(secrets.token_urlsafe(32))')")
    log.info("Starting chatbot...")
    try:
        from indexer.embedder import check_ollama_embed
        if check_ollama_embed():
            stats = get_stats()
            log.info(f"Ready. Ollama reachable. Vector store: {stats['total_chunks']} chunks.")
        else:
            log.warning("Ollama not reachable at startup. Make sure 'ollama serve' is running.")
    except Exception:
        log.exception("Error during startup")
    await asyncio.to_thread(quota.load)

    async def _expire_stats_forever():
        while True:
            expire_and_save_stats()   # in the loop thread, where the counters are changed
            await asyncio.sleep(6 * 3600)

    stats_task = asyncio.create_task(_expire_stats_forever())

    # Graceful shutdown: waits for in-flight requests to complete
    shutdown_event = asyncio.Event()

    def _handle_sigterm(*_):
        log.info("SIGTERM received: waiting for in-flight requests to complete...")
        shutdown_event.set()

    signal.signal(signal.SIGTERM, _handle_sigterm)

    yield

    stats_task.cancel()
    if shutdown_event.is_set():
        await asyncio.sleep(2)  # window for streaming requests to complete

    log.info("Chatbot stopped.")


app = FastAPI(
    title=f"Zautte – {SITE_NAME}" if SITE_NAME else "Zautte",
    description="API di Zautte, assistente virtuale RAG.",
    version="1.0.0",
    lifespan=lifespan,
    # /docs and /openapi.json only with API_DOCS=true (development)
    docs_url="/docs" if API_DOCS else None,
    openapi_url="/openapi.json" if API_DOCS else None,
    redoc_url=None,
)

_panel_dir = Path(__file__).parent.parent / "admin_panel"
_instance = Path(INSTANCE_DIR) if INSTANCE_DIR else None


def _panel_file(name: str, optional: bool = False) -> str:
    """Restricted-area file: the installation's (INSTANCE_DIR/admin_panel)
    if present, otherwise the generic one from the repository. With optional=True a
    missing file yields an empty string."""
    for base in ([_instance / "admin_panel"] if _instance else []) + [_panel_dir]:
        if (base / name).is_file():
            return (base / name).read_text(encoding="utf-8")
    if optional:
        return ""
    raise FileNotFoundError(name)


if ADMIN_PATH:
    # Restricted-area address: it comes from .env and is not written in the code.
    @app.get(ADMIN_PATH, include_in_schema=False)
    async def area_page():
        """Page with only the key form. The rest (panel, script,
        styles) is received from <address>/dati with a valid key."""
        try:
            html = _panel_file("login.html")
        except OSError as e:
            log.error("Login page not readable: %s", e)
            raise HTTPException(status_code=404, detail="Not Found")
        return HTMLResponse(html, headers={
            "Cache-Control": "no-store",
            # not embeddable in other pages, no <base> or plugin injection, forms only to us
            "Content-Security-Policy": "frame-ancestors 'none'; base-uri 'none'; object-src 'none'; form-action 'self'",
        })

    @app.get(ADMIN_PATH + "/dati", include_in_schema=False)
    async def area_dati(_: None = Security(require_admin)):
        """Styles, HTML and JavaScript of the restricted area: they are in no public
        page, they are received only with a valid key (and with the address not blocked)."""
        try:
            parts = {k: _panel_file("panel." + k) for k in ("css", "html", "js")}
            # installation changelog: optional, at the end of the panel
            parts["html"] += _panel_file("changelog.html", optional=True)
        except OSError as e:
            log.error("Dashboard not readable: %s", e)
            raise HTTPException(status_code=500, detail="Pannello non disponibile")
        return JSONResponse(parts, headers={"Cache-Control": "no-store"})


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    """Headers that apply to every response."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    # Anything asked with a key or a token (statistics, messages, answers) is for that
    # requester only: no proxy or browser cache may keep it
    if request.headers.get("x-admin-key") or request.headers.get("authorization"):
        response.headers.setdefault("Cache-Control", "private, no-store")
    return response


class _MaskSecretsInAccessLog(logging.Filter):
    """The password-reset link carries its token in the query string: uvicorn would
    write it to the access log (kept for a year). Any `token=` / `key=` value is hidden."""
    _SECRET = re.compile(r"([?&](?:token|key)=)[^&\s\"]+", re.IGNORECASE)

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(self._SECRET.sub(r"\1[hidden]", a) if isinstance(a, str) else a
                                for a in record.args)
        return True


logging.getLogger("uvicorn.access").addFilter(_MaskSecretsInAccessLog())


app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


_NOT_FOUND_PAGE = (
    '<!DOCTYPE html><html lang="it"><head><meta charset="utf-8"><title>Pagina non trovata</title>'
    '<meta name="robots" content="noindex"></head><body style="font-family:Arial,sans-serif;'
    'text-align:center;padding:15vh 20px;color:#333"><h1 style="color:#003366">404</h1>'
    '<p>La pagina richiesta non esiste.</p></body></html>'
)


@app.exception_handler(StarletteHTTPException)
async def not_found_page(request: Request, exc: StarletteHTTPException):
    """Nonexistent path or method: generic page instead of FastAPI's JSON
    (does not reveal which framework the service is written with). Errors raised on purpose by the
    endpoints, with their own message, stay in JSON."""
    if (exc.status_code, exc.detail) in ((404, "Not Found"), (405, "Method Not Allowed")):
        return HTMLResponse(_NOT_FOUND_PAGE, status_code=exc.status_code)
    return await http_exception_handler(request, exc)

class _RevalidatedStaticFiles(StaticFiles):
    """Static files with Cache-Control: no-cache: the browser may cache them but
    revalidates them on every open (304 response if unchanged), so updated pages and
    scripts arrive immediately without forcing a reload."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


class _OverlayStaticFiles(_RevalidatedStaticFiles):
    """Like _RevalidatedStaticFiles, but first looks for the file in the installation's
    folder (INSTANCE_DIR/widget) and uses the generic one only if it is missing."""

    def __init__(self, directory: str, overlay: str | None = None):
        super().__init__(directory=directory)
        self._overlay = StaticFiles(directory=overlay) if overlay else None

    async def get_response(self, path, scope):
        if self._overlay is not None:
            try:
                response = await self._overlay.get_response(path, scope)
                if response.status_code != 404:
                    response.headers["Cache-Control"] = "no-cache"
                    return response
            except StarletteHTTPException as e:
                if e.status_code != 404:
                    raise
        return await super().get_response(path, scope)


# Serves the JS widget as a static file (GET /widget/chatbot-widget.js)
_widget_dir = Path(__file__).parent.parent / "widget"
_widget_overlay = _instance / "widget" if _instance and (_instance / "widget").is_dir() else None
if _widget_dir.exists():
    app.mount("/widget", _OverlayStaticFiles(str(_widget_dir), str(_widget_overlay) if _widget_overlay else None),
              name="widget")


@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
async def root(request: Request):
    """Whoever opens just the domain (https://bot.example.it/) lands on the login
    page instead of a 404. Temporary (302): the root may serve something else.
    The query string passes through intact (e.g. ?tracker_install_check=… from the Matomo test)."""
    query = request.url.query
    return RedirectResponse("/widget/dashboard.html" + ("?" + query if query else ""), status_code=302)

app.add_middleware(
    CORSMiddleware,
    allow_origins=API_CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

# User authentication router (POST /auth/login, GET /auth/me)
app.include_router(auth.router)


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------

class FeedbackRequest(BaseModel):
    question: str = Field(..., max_length=1000)
    answer: str = Field(..., max_length=5000)
    rating: int = Field(..., ge=-1, le=1, description="-1=negativo, 1=positivo")
    comment: str | None = Field(None, max_length=2000,
                                description="perché la risposta non va bene (facoltativo)")
    urls: list[str] = Field(default_factory=list,
                            description="pagine con l'informazione corretta (facoltative)")
    rid: str | None = Field(None, max_length=32,
                            description="identificativo della domanda, dalla risposta di /chat")


class FeedbackDetailRequest(BaseModel):
    id: str = Field(..., max_length=32)
    comment: str | None = Field(None, max_length=2000)
    urls: list[str] = Field(default_factory=list)


_HISTORY_MAX_CHARS = 2000


class HistoryMessage(BaseModel):
    role: str = Field(..., pattern="^(user|assistant)$")
    content: str

    @field_validator("content")
    @classmethod
    def _truncate(cls, v: str) -> str:
        # The widget sends back the bot's answers in full: beyond the limit we truncate
        # instead of rejecting (with max_length the question following a long
        # answer failed with 422).
        return v[:_HISTORY_MAX_CHARS]


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=1000,
                          description="La domanda del visitatore")
    history: list[HistoryMessage] = Field(
        default=[],
        max_length=6,
        description="Ignorata: la cronologia la tiene il server (campo cid)"
    )
    cid: str = Field(default="", max_length=32,
                     description="Identificativo di conversazione ricevuto con la risposta precedente")


class Source(BaseModel):
    title: str
    url: str
    score: float


class ChatResponse(BaseModel):
    answer: str
    sources: list[Source]
    language: str
    rid: str = ""
    cid: str = ""
    blocked: bool = False


class ForgetRequest(BaseModel):
    rids: list[str] = Field(default_factory=list, max_length=1000)
    uid: str | None = Field(None, max_length=64)
    last: int = Field(0, ge=0, le=100000, description="le ultime N domande dell'utente")
    all: bool = Field(False, description="tutte le domande e i feedback dell'utente")
    ts: list[str] = Field(default_factory=list, max_length=1000,
                          description="timestamp esatti delle voci di usage.jsonl dell'utente")
    dry_run: bool = False


_RID_RE = re.compile(r"[0-9a-f]{12}")


def _take_question(request: Request, user: dict) -> str:
    """Counts the question against the daily limit (api/quota.py) and returns its
    key, so it can be given back if the answer fails; 429 if today's limit is
    already reached (the widget shows the message with the number of questions)."""
    key = quota.key_for(user, request.client.host if request.client else "")
    if not quota.take(key):
        log.info("Limit of %d questions per day reached (%s)",
                 DAILY_QUESTION_LIMIT, "user" if key.startswith("uid:") else "IP")
        raise HTTPException(
            status_code=429,
            detail={"error": "daily_limit", "limit": DAILY_QUESTION_LIMIT},
            headers={"Retry-After": str(quota.seconds_to_midnight())},
        )
    return key


def _take_slot(request: Request, user: dict) -> str:
    """A slot for an answer in progress (api/llm_gate.py); 429 "busy" if the user or the
    service has too many answers at once. Nothing is counted against the daily limit."""
    who = user.get("uid") or (request.client.host if request.client else "")
    sid = llm_gate.acquire(who)
    if sid is None:
        raise HTTPException(status_code=429, detail={"error": "busy"}, headers={"Retry-After": "5"})
    return sid


def _blocked_for_pii(question: str) -> str | None:
    """If the question contains personal data (PII_BLOCK) returns the rid with which
    it was discarded: no search, no model call, the text is not
    recorded and the daily limit is not touched."""
    if not contains_personal(question):
        return None
    rid = secrets.token_hex(6)
    log.info("Question %s discarded: contains personal data", rid)
    return rid


def _new_rid() -> str:
    """Identifier of a question: the same in usage.jsonl, gaps.jsonl,
    feedback.jsonl and the API log (see api/datalog.py)."""
    return secrets.token_hex(6)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

def _read_jsonl(path: Path) -> list[dict]:
    """Reads a JSONL file tolerating empty or malformed lines (append-only file:
    a line may be left truncated by a crash). Synchronous function: in async
    endpoints call it with asyncio.to_thread so as not to block the event loop."""
    entries: list[dict] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    log.debug("Malformed JSONL line in %s ignored", path.name)
                    continue
                if isinstance(obj, dict):
                    entries.append(obj)
    except OSError as e:
        log.warning("Could not read %s: %s", path, e)
    return entries


def _grep_lines(path: Path, markers: tuple[str, ...]) -> list[str]:
    """Lines of `path` containing at least one of the markers (read line by line,
    the file may weigh tens of MB). Synchronous: call with asyncio.to_thread."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return [ln.rstrip("\n") for ln in fh if any(m in ln for m in markers)]
    except OSError as e:
        log.warning("Could not read %s: %s", path, e)
        return []


def _read_gaps_count() -> int:
    f = _DATA_DIR / "gaps.jsonl"
    return len(_read_jsonl(f)) if f.exists() else 0


def _read_recent_gaps(n: int) -> list[dict]:
    f = _DATA_DIR / "gaps.jsonl"
    if not f.exists():
        return []
    recent = []
    for e in reversed(_read_jsonl(f)):
        if "query" in e:
            recent.append({"ts": e.get("ts", ""), "query": e["query"]})
        if len(recent) >= n:
            break
    return list(reversed(recent))


def _read_feedback_summary() -> dict:
    """Panel counters: `negative` counts only thumbs-down NOT yet
    resolved by the administrator (since 10/10/2026); resolved ones are in
    `resolved`, outside the count and the total. One vote per user and
    answer (since 12/10/2026: repeated clicks on the thumb do not add up)."""
    from api.feedback_store import dedup
    f = _DATA_DIR / "feedback.jsonl"
    pos = neg = res = 0
    if f.exists():
        resolved = _load_resolved_negative()
        for e in dedup(_read_jsonl(f)):
            if e.get("rating") == 1:
                pos += 1
            elif e.get("rating") == -1:
                if _resolved_key(e.get("ts", ""), e.get("question", "")) in resolved:
                    res += 1
                else:
                    neg += 1
    return {"total": pos + neg, "positive": pos, "negative": neg, "resolved": res}


def _load_user_names() -> dict[str, str]:
    """Map of user id -> name from data/users.json (empty if absent).

    Used only in admin responses: the data files (usage.jsonl, stats.json)
    contain the opaque id, the name is resolved at display time."""
    users_file = _DATA_DIR / "users.json"
    names: dict[str, str] = {}
    if users_file.exists():
        try:
            for u in json.loads(users_file.read_text(encoding="utf-8")):
                names[u.get("id")] = u.get("name", "")
        except (OSError, ValueError, TypeError, AttributeError) as e:
            log.warning("users.json not readable: %s", e)
    return names


def _log_usage(uid: str, question: str, lang: str | None = None,
               response_ms: int | None = None, rid: str | None = None) -> None:
    """Records a per-user usage event in data/usage.jsonl.

    Includes the TEXT of the question (for the pilot review), in addition to the opaque
    user id, question identifier, timestamp and metrics. Name/email stay
    in users.json and are resolved at display time only in the dashboard (admin view).
    """
    if not uid or uid == "anon":
        return
    f = _DATA_DIR / "usage.jsonl"
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        question = question or ""
        entry: dict = {
            "ts": now_local().isoformat(timespec="seconds"),
            "uid": uid,
            "q": question[:1000],
            "q_len": len(question),
        }
        if lang:
            entry["lang"] = lang
        if response_ms is not None:
            entry["response_ms"] = response_ms
        if rid:
            entry["rid"] = rid
        with file_lock(f), open(f, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except (OSError, TypeError, ValueError) as e:
        log.warning("Usage logging failed: %s", e)


def _log_answer(uid: str, rid: str, text: str, sources: list | None = None,
                partial: bool = False) -> None:
    """Records the answer given to a question in data/answers.jsonl (since 07/10/2026).

    One line per answer: time, opaque user id, the question's `rid` (the same as in
    usage.jsonl), answer text as the user saw it, and sources
    (title and link). Only for authenticated users, like usage.jsonl. With
    `partial` the answer was interrupted (error or user gone) and the text
    is what arrived up to that point. The answer is not masked: the
    question already is, before the model, and the text comes from the public
    content of the body (office contact details included).
    """
    if not uid or uid == "anon" or not rid:
        return
    f = _DATA_DIR / "answers.jsonl"
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        entry: dict = {
            "ts": now_local().isoformat(timespec="seconds"),
            "uid": uid,
            "rid": rid,
            "a": (text or "")[:20000],
            "sources": [{"title": s.get("title", ""), "url": s.get("url", "")}
                        for s in (sources or []) if isinstance(s, dict)],
        }
        if partial:
            entry["partial"] = True
        with file_lock(f), open(f, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except (OSError, TypeError, ValueError) as e:
        log.warning("Answer logging failed: %s", e)


@app.get("/health")
async def health(request: Request, key: str | None = Security(_api_key_header)):
    """Public liveness (minimal). Operational details (statistics, queries,
    feedback, costs) are returned ONLY with a valid admin key, so as not to
    expose them to anyone who knows the URL."""
    base = {
        "status": "ok",
        "uptime_seconds": int((now_local() - _startup_time).total_seconds()),
    }

    is_admin = check_admin_key(request, key)
    if not is_admin:
        return base

    stats = get_stats()
    llm_info = {"ollama": OLLAMA_MODEL, "bedrock": BEDROCK_MODEL}.get(LLM_PROVIDER, CLAUDE_MODEL)
    last_indexed = None
    if EMBEDDINGS_FILE.exists():
        mtime = EMBEDDINGS_FILE.stat().st_mtime
        last_indexed = (datetime.datetime.fromtimestamp(mtime, tz=datetime.UTC)
                        .astimezone().strftime("%d/%m/%Y %H:%M"))

    # Token history: resolve the user id to a name (only here, admin view).
    activity = get_activity_stats()
    names = _load_user_names()
    activity["token_history"] = [
        {**e, "user": names.get(e.get("uid"), e.get("uid", "")) or ""}
        for e in activity.get("token_history", [])
    ]

    base.update({
        "llm_provider": LLM_PROVIDER,
        "hybrid_search": is_bm25_active(),
        "indexed_chunks": stats["total_chunks"],
        "unique_sources": stats["unique_sources"],
        "doc_types": stats["doc_types"],
        "llm_model": llm_info,
        "last_indexed": last_indexed,
        "queries_since_restart": get_query_count(),
        "gaps_total": _read_gaps_count(),
        "gaps_recent": _read_recent_gaps(5),
        "feedback": _read_feedback_summary(),
        "activity": activity,
        "top_doc": get_top_doc(),
    })
    return base


@app.get("/client-ip")
async def client_ip(request: Request):
    """Client IP address as seen by the API, shown in the footer of the
    pages. Behind the load balancer it is the one forwarded in X-Forwarded-For (only
    from the addresses in FORWARDED_ALLOW_IPS). Public: it tells each caller only their
    own address."""
    ip = request.client.host if request.client else ""
    return JSONResponse({"ip": ip}, headers={"Cache-Control": "no-store"})


@app.post("/admin/reload")
async def admin_reload(_: None = Security(require_admin)):
    """Reloads the vector store from disk without restarting the API: used by
    `scripts.sync news` after indexing the new news items. Response: the
    store statistics and `reloaded` (false if the files were unreadable and
    the previous version stays in memory)."""
    from api.rag import clear_response_cache
    from indexer.vector_store import reload
    result = await asyncio.to_thread(reload)
    if result.get("reloaded"):
        clear_response_cache()   # cached answers may refer to passages the new index dropped
    return {"ok": True, **result}


@app.get("/stats")
async def stats(_: None = Security(require_admin)):
    """Statistics on the indexed content."""
    return get_stats()


@app.get("/gaps")
async def gaps(limit: int = 50, _: None = Security(require_admin)):
    """Unanswered queries — useful to identify content gaps."""
    gaps_file = _DATA_DIR / "gaps.jsonl"
    if not gaps_file.exists():
        return {"gaps": [], "total": 0}
    entries = await asyncio.to_thread(_read_jsonl, gaps_file)
    return {"gaps": entries[-limit:], "total": len(entries)}


@app.post("/feedback")
@limiter.limit("60/hour")
async def feedback(request: Request, req: FeedbackRequest, background_tasks: BackgroundTasks,
                   user: dict = Security(require_user)):
    """Saves the user's feedback (thumbs up/down). With login enabled it also records
    who gave it (the colleague's name), so the administrator knows whom
    to ask. Comment and URL may arrive here or later, with
    POST /feedback/detail. A second vote by the same user on the same
    answer (same rid) replaces the first instead of adding to it. Returns
    the id of the feedback just saved."""
    from api import feedback_store as fs
    entry = {
        "ts": fs.now_iso(),
        "rating": req.rating,
        "question": redact(req.question)[:200],
        "answer_preview": req.answer[:100],
        "uid": user.get("uid", ""),
        "user": user.get("name", ""),
    }
    if req.rid and _RID_RE.fullmatch(req.rid):
        from api.datalog import answer_owner
        owner = await asyncio.to_thread(answer_owner, req.rid)
        # with login, the vote must be on an answer that was given to this user
        if (owner and owner != entry["uid"]) or (AUTH_ENABLED and not owner):
            raise HTTPException(status_code=403, detail="Risposta non trovata per questo utente")
        entry["rid"] = req.rid
    elif AUTH_ENABLED:
        raise HTTPException(status_code=400, detail="Identificativo della risposta mancante")
    comment = fs.clean_comment(req.comment)
    urls = fs.clean_urls(req.urls[:20])
    if comment:
        entry["comment"] = comment
    if urls:
        entry["urls"] = urls
    entry = await asyncio.to_thread(fs.append, entry)
    if req.rating == -1 and (comment or urls):
        background_tasks.add_task(_notify_feedback, entry)
    return {"ok": True, "id": entry["id"]}


@app.post("/feedback/detail")
@limiter.limit("60/hour")
async def feedback_detail(request: Request, req: FeedbackDetailRequest, background_tasks: BackgroundTasks,
                          user: dict = Security(require_user)):
    """Attaches to an already recorded feedback the reason (free text) and the links
    to the pages with the correct information. Only the feedback's author.
    If FEEDBACK_NOTIFY_EMAIL is set, notifies the administrator by email."""
    from api import feedback_store as fs
    comment = fs.clean_comment(req.comment)
    urls = fs.clean_urls(req.urls[:20])
    if not comment and not urls:
        raise HTTPException(status_code=400, detail="Nessun dettaglio da salvare")
    entry = await asyncio.to_thread(fs.attach_details, req.id, user.get("uid", ""), comment, urls)
    if entry is None:
        raise HTTPException(status_code=404, detail="Feedback non trovato")
    background_tasks.add_task(_notify_feedback, entry)
    return {"ok": True}


def _notify_feedback(entry: dict) -> None:
    """Email to the administrator for a report. Runs in the background after
    the HTTP response: a slow or down relay must never block the colleague."""
    from api.mailer import send_feedback_notification, smtp_configured
    from config.settings import FEEDBACK_NOTIFY_EMAIL, PILOT_LOGIN_URL
    if not FEEDBACK_NOTIFY_EMAIL or not smtp_configured():
        return
    try:
        send_feedback_notification(FEEDBACK_NOTIFY_EMAIL, entry, PILOT_LOGIN_URL)
    except Exception:
        log.warning("Feedback notification not sent to %s", FEEDBACK_NOTIFY_EMAIL, exc_info=True)


def _resolved_negative_file():
    from api.feedback_store import RESOLVED_FILE
    return RESOLVED_FILE


def _load_resolved_negative() -> set[str]:
    """Returns the set of ts::question keys already resolved."""
    from api.feedback_store import load_resolved
    return load_resolved()


def _resolved_key(ts: str, question: str) -> str:
    from api.feedback_store import resolved_key
    return resolved_key(ts, question)


@app.get("/feedback/negative")
async def feedback_negative(limit: int = 200, _: None = Security(require_admin)):
    """Questions with negative feedback not yet resolved, with author, comment
    and links reported by the colleague. Admin only."""
    from api import feedback_store as fs
    items, open_negative, total = fs.negative_open(_load_resolved_negative(), _resolved_key, limit)
    return {"items": items, "total_negative": open_negative, "total": total}


class ResolveRequest(BaseModel):
    ts: str = Field(..., max_length=30)
    question: str = Field(..., max_length=1000)
    note: str | None = Field(None, max_length=1000,
                             description="nota per il collega, inclusa nell'email di riscontro")
    notify: bool = Field(True, description="invia l'email di riscontro a chi ha segnalato")


@app.post("/feedback/resolve")
async def feedback_resolve(req: ResolveRequest, background_tasks: BackgroundTasks,
                           _: None = Security(require_admin)):
    """Marks a negative feedback as resolved — admin only. If the feedback has
    an author with an email (login enabled) and `notify` is true, sends them a
    reply email with the optional note. Returns `notified` = email being sent."""
    import json as _json

    from api import feedback_store as fs
    f = _resolved_negative_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    existing: list[dict] = []
    if f.exists():
        try:
            existing = _json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = []
    key = _resolved_key(req.ts, req.question)
    if any(e.get("key") == key for e in existing):
        return {"ok": True, "notified": False}

    note = (req.note or "").strip()[:1000]
    record = {"key": key, "resolved_at": fs.now_iso()}
    if note:
        record["note"] = note
    notified_to = ""
    entry = fs.find_entry(req.ts, req.question)
    if req.notify and entry and entry.get("uid"):
        from api.auth import get_user_by_id
        u = get_user_by_id(entry["uid"])
        if u and u.get("email"):
            notified_to = u["email"]
            background_tasks.add_task(_notify_resolved, u["email"], u.get("name") or entry.get("user", ""),
                                      entry, note)
    if notified_to:
        record["notified"] = notified_to
    existing.append(record)
    write_text_atomic(f, _json.dumps(existing, ensure_ascii=False, indent=2))
    return {"ok": True, "notified": bool(notified_to)}


def _notify_resolved(to: str, name: str, entry: dict, note: str) -> None:
    """Reply email to the colleague, in the background. Replies go
    to the administrator (Reply-To = FEEDBACK_NOTIFY_EMAIL)."""
    from api.mailer import send_feedback_resolved, smtp_configured
    from config.settings import FEEDBACK_NOTIFY_EMAIL, PILOT_LOGIN_URL
    if not smtp_configured():
        return
    try:
        send_feedback_resolved(to, name, entry, note, PILOT_LOGIN_URL, reply_to=FEEDBACK_NOTIFY_EMAIL)
    except Exception:
        log.warning("Acknowledgement email not sent to %s", to, exc_info=True)


@app.get("/feedback/list")
async def feedback_list(limit: int = 100, _: None = Security(require_admin)):
    """List of feedback received — admin only. One vote per user and answer."""
    from api.feedback_store import dedup
    feedback_file = _DATA_DIR / "feedback.jsonl"
    if not feedback_file.exists():
        return {"feedback": [], "total": 0, "positive": 0, "negative": 0}
    entries = dedup(await asyncio.to_thread(_read_jsonl, feedback_file))
    positive = sum(1 for e in entries if e.get("rating") == 1)
    negative = sum(1 for e in entries if e.get("rating") == -1)
    return {
        "feedback": entries[-limit:],
        "total": len(entries),
        "positive": positive,
        "negative": negative,
    }


@app.get("/usage/summary")
async def usage_summary(_: None = Security(require_admin)):
    """Per-user usage summary — admin only.

    Aggregates data/usage.jsonl (opaque ids + metrics) resolving names from
    data/users.json. Returns: messages per user, first/last access,
    active days, and the daily trend (active users + messages).
    """
    from collections import defaultdict

    usage_file = _DATA_DIR / "usage.jsonl"
    names = _load_user_names()

    per_user: dict[str, dict] = defaultdict(
        lambda: {"messages": 0, "first_seen": None, "last_seen": None, "days": set()}
    )
    day_users: dict[str, set] = defaultdict(set)
    day_msgs: dict[str, int] = defaultdict(int)
    total = 0

    entries = await asyncio.to_thread(_read_jsonl, usage_file) if usage_file.exists() else []
    for e in entries:
        uid = e.get("uid")
        ts = e.get("ts", "")
        if not uid:
            continue
        day = ts[:10]
        u = per_user[uid]
        u["messages"] += 1
        u["days"].add(day)
        if u["first_seen"] is None or ts < u["first_seen"]:
            u["first_seen"] = ts
        if u["last_seen"] is None or ts > u["last_seen"]:
            u["last_seen"] = ts
        day_users[day].add(uid)
        day_msgs[day] += 1
        total += 1

    # Also include registered users who have not used the bot yet
    # (they appear with 0 messages and an empty "ultimo accesso").
    for uid in names:
        _ = per_user[uid]  # defaultdict creates the entry at zero if absent

    users_out = [
        {
            "uid": uid,
            "name": names.get(uid, uid),
            "messages": u["messages"],
            "active_days": len(u["days"]),
            "first_seen": u["first_seen"],
            "last_seen": u["last_seen"],
        }
        for uid, u in per_user.items()
    ]
    # sort by messages (desc) and, on ties, by name
    users_out.sort(key=lambda x: (-x["messages"], (x["name"] or "").lower()))

    active = sum(1 for u in per_user.values() if u["messages"] > 0)

    daily = [
        {"day": d, "users": len(day_users[d]), "messages": day_msgs[d]}
        for d in sorted(day_users)
    ]

    return {
        "total_messages": total,
        "total_users": len(per_user),
        "total_registered": len(names),
        "total_active": active,
        "users": users_out,
        "daily": daily[-30:],
    }


@app.get("/usage/messages")
async def usage_messages(limit: int = 300, _: None = Security(require_admin)):
    """Log of the messages typed by users (text + timestamp + user) with the
    answer given by Zautte — admin only.

    Reads data/usage.jsonl resolving names from data/users.json and matches by
    `rid` the answer and sources from data/answers.jsonl (since 07/10/2026; before
    that `a` is missing). Older entries (recorded before the question text
    was introduced) are ignored.
    """
    usage_file = _DATA_DIR / "usage.jsonl"
    answers_file = _DATA_DIR / "answers.jsonl"
    names = _load_user_names()

    entries = await asyncio.to_thread(_read_jsonl, usage_file) if usage_file.exists() else []
    answers = (await asyncio.to_thread(_read_jsonl, answers_file)
               if answers_file.exists() else [])
    by_rid = {a.get("rid"): a for a in answers if a.get("rid")}
    msgs: list[dict] = []
    for e in entries:
        if "q" not in e:  # old entries without the question text
            continue
        uid = e.get("uid", "")
        m = {
            "ts": e.get("ts", ""),
            "uid": uid,
            "name": names.get(uid, uid),
            "q": e.get("q", ""),
            "lang": e.get("lang"),
            "response_ms": e.get("response_ms"),
            "rid": e.get("rid", ""),
        }
        a = by_rid.get(e.get("rid"))
        if a:
            m["a"] = a.get("a", "")
            m["sources"] = a.get("sources", [])
            if a.get("partial"):
                m["partial"] = True
        msgs.append(m)

    total = len(msgs)
    recent = msgs[-limit:]
    recent.reverse()  # most recent first
    return {"messages": recent, "total": total}


@app.post("/usage/forget")
async def usage_forget(req: ForgetRequest, _: None = Security(require_admin)):
    """Deletes the chosen questions on request — admin only.

    Selection: `rids`, or `uid` with `last` (the last N), `all` (all of them, with
    their feedback) or `ts` (exact timestamps of the usage.jsonl entries).
    Removes the entries from usage.jsonl, answers.jsonl, gaps.jsonl, feedback.jsonl and
    the «risolto» marks, then from the process memory: counter of the most
    frequent questions (ends up in stats.json) and answer cache. The API log
    contains only the identifier, not the text. With `dry_run` it only returns
    what would be removed. Assumes a single process (api/serve.py): with several
    workers the others' memory would stay as is until restart.
    """
    if not req.rids and not (req.uid and (req.last or req.all or req.ts)):
        raise HTTPException(status_code=400, detail="Indicare rids oppure uid con last, all o ts")
    # Synchronous on purpose: while it rewrites the files this process does not append
    # other lines (writes from the other process are recovered by rewrite_jsonl).
    from api.datalog import answer_owner
    owners = {req.uid} if req.uid else {answer_owner(r) for r in req.rids} - {None}
    result = forget(rids=req.rids, uid=req.uid, last=req.last, every=req.all, ts=req.ts,
                    dry_run=req.dry_run)
    memory = 0
    if not req.dry_run:
        memory = forget_queries([q["q"] for q in result["questions"]])
        for owner in owners:   # the questions may still be in the conversations of their authors
            conversations.forget(owner)
        if req.uid and req.all:   # every question of the user: their usage records go too
            memory += forget_usage_of({req.uid})
        log.info("Deletion on request: questions %d, usage entries removed %d, answers %d, "
                 "gaps %d, feedback %d, from memory %d", len(result["questions"]),
                 result["usage"], result["answers"], result["gaps"], result["feedback"], memory)
    return {**result, "memory": memory, "dry_run": req.dry_run}


@app.get("/crawl-history")
async def crawl_history(_: None = Security(require_admin)):
    """Crawl and indexing history (latest events from the sync log). Admin only."""
    log_path = Path("/var/log/chatbot-sync.log")
    if not log_path.exists():
        return {"events": [], "current_html": None, "current_pdf": None}

    # Line-by-line reading: memory-efficient on 50MB files,
    # filters in Python without depending on subprocess/grep
    # Old log lines are in Italian and stay in the files for a while: both forms are read
    MARKERS = ("Vector store loaded:", "Vector store caricato:", "Crawl incrementale completato",
               "Incremental crawl completed", "=== Crawl", "[HTML ", "[PDF ")

    lines = await asyncio.to_thread(_grep_lines, log_path, MARKERS)

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
            msg = line.strip()   # lines without a timestamp (e.g. "=== Crawl ... ===")

        if ("Crawl incrementale completato" in msg or "Incremental crawl completed" in msg
                or "=== Crawl" in msg):
            events.append({"ts": last_ts, "type": "crawl_end",
                           "label": "HTML crawl completed"})
        elif "Vector store loaded:" in msg or "Vector store caricato:" in msg:
            m2 = re.search(r"(\d+) chunk", msg)
            if m2:
                n = int(m2.group(1))
                events.append({"ts": last_ts, "type": "vs_loaded", "chunks": n,
                               "label": f"Vector store: {n:,} chunks"})
        else:
            mh = html_re.search(msg)
            if mh:
                cur, tot = int(mh.group(1)), int(mh.group(2))
                last_html = {"ts": last_ts, "current": cur, "total": tot}
                if cur == tot:
                    events.append({"ts": last_ts, "type": "html_done",
                                   "label": f"HTML: {tot:,} pages completed",
                                   "pages": tot})
                continue
            mp = pdf_re.search(msg)
            if mp:
                cur, tot = int(mp.group(1)), int(mp.group(2))
                last_pdf = {"ts": last_ts, "current": cur, "total": tot}

    # Deduplicate VS events (keep only the chunk count changes)
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
async def chat(request: Request, req: ChatRequest,
               user: dict = Security(require_user)):
    """
    Complete answer to the visitor's question.
    Waits for the whole answer before returning.
    """
    import time as _time
    blocked = _blocked_for_pii(req.question)
    if blocked:
        return {"answer": BLOCK_MESSAGE, "sources": [], "language": "it",
                "rid": blocked, "blocked": True}
    slot = _take_slot(request, user)
    try:
        key = _take_question(request, user)
    except HTTPException:
        llm_gate.release(slot)
        raise
    try:
        # Recognisable personal data masked before the model and the logs
        question = redact(req.question)
        cid, history = conversations.resolve(user.get("uid", ""), req.cid)
        rid = _new_rid()
        _t0 = _time.monotonic()
        result = await answer(question, stream=False, history=history,
                              uid=user.get("uid"), rid=rid)
        _lang = result.get("language") if isinstance(result, dict) else None
        await asyncio.to_thread(_log_usage, user.get("uid"), question, _lang,
                                int((_time.monotonic() - _t0) * 1000), rid=rid)
        await asyncio.to_thread(_log_answer, user.get("uid"), rid, result.get("answer", ""),
                                result.get("sources"))
        conversations.add_turn(user.get("uid", ""), cid, question, result.get("answer", ""))
        return {**result, "rid": rid, "cid": cid}
    except ValueError as e:
        quota.give_back(key)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        quota.give_back(key)
        log.exception("Error in /chat")
        raise HTTPException(status_code=500, detail="Errore interno del server")
    finally:
        llm_gate.release(slot)


@app.post("/chat/stream")
async def chat_stream(request: Request, req: ChatRequest,
                      user: dict = Security(require_user)):
    """
    Streaming answer via Server-Sent Events (SSE).
    The widget receives tokens as they are generated.

    SSE format:
      data: {"token": "..."}\n\n        → text token
      data: {"sources": [...], "rid": "...", "language": "it"}\n\n  → sources, question identifier, language
      data: {"done": true}\n\n          → end of stream
    """
    blocked = _blocked_for_pii(req.question)
    if blocked:
        async def _blocked_stream():
            yield f"data: {json.dumps({'token': BLOCK_MESSAGE}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'sources': [], 'rid': blocked, 'language': 'it', 'blocked': True})}\n\n"
            yield 'data: {"done": true}\n\n'
        return StreamingResponse(_blocked_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    slot = _take_slot(request, user)
    try:
        key = _take_question(request, user)
    except HTTPException:
        llm_gate.release(slot)
        raise
    try:
        # Recognisable personal data masked before the model and the logs
        question = redact(req.question)
        cid, history = conversations.resolve(user.get("uid", ""), req.cid)
        rid = _new_rid()
        generator, sources, language = await answer(question, stream=True, history=history,
                                                    uid=user.get("uid"), rid=rid)
        await asyncio.to_thread(_log_usage, user.get("uid"), question, language, rid=rid)
    except ValueError as e:
        quota.give_back(key)
        llm_gate.release(slot)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        quota.give_back(key)
        llm_gate.release(slot)
        log.exception("Error in /chat/stream")
        raise HTTPException(status_code=500, detail="Errore interno del server")

    async def event_stream():
        import asyncio as _asyncio

        queue: _asyncio.Queue = _asyncio.Queue()

        parts: list[str] = []   # answer text, for answers.jsonl
        completed = False

        async def _produce():
            nonlocal completed
            try:
                async for token in generator:
                    parts.append(token)
                    await queue.put(("token", token))
                completed = True
                conversations.add_turn(user.get("uid", ""), cid, question, "".join(parts))
                await queue.put(("sources", None))
                await queue.put(("done", None))
            except Exception as exc:
                log.warning("Error while streaming the answer", exc_info=True)
                quota.give_back(key)
                await queue.put(("error", str(exc)))
            finally:
                llm_gate.release(slot)
                # Even if interrupted (error, user gone): we record
                # what was generated, marked as partial.
                await asyncio.to_thread(_log_answer, user.get("uid"), rid, "".join(parts),
                                        sources, partial=not completed)

        task = _asyncio.create_task(_produce())
        try:
            while True:
                try:
                    kind, value = await _asyncio.wait_for(queue.get(), timeout=15.0)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue

                if kind == "token":
                    yield f"data: {json.dumps({'token': value}, ensure_ascii=False)}\n\n"
                elif kind == "sources":
                    sources_data = json.dumps(
                        {"sources": [s.__dict__ if hasattr(s, '__dict__') else s
                                     for s in sources],
                         "rid": rid, "cid": cid, "language": language},
                        ensure_ascii=False,
                    )
                    yield f"data: {sources_data}\n\n"
                    yield 'data: {"done": true}\n\n'
                    break
                elif kind == "done":
                    yield 'data: {"done": true}\n\n'
                    break
                elif kind == "error":
                    log.error(f"Error while streaming: {value}")
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
            "X-Accel-Buffering": "no",  # important for nginx
        },
    )
