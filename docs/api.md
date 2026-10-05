<sub>[Zautte](../README.md) › [Documentation](README.md) › Backend API</sub>

# Backend API

`api/main.py` (with `api/auth.py`) — FastAPI with SSE streaming, rate limiting, user login and admin authentication.

## Starting

```sh
# Development
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

# Production (FreeBSD): `service chatbot start` runs start.sh under daemon(8);
# start.sh launches two single-worker uvicorn processes on port 8000:
/opt/chatbot/venv/bin/python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 &   # IPv4
/opt/chatbot/venv/bin/python -m uvicorn api.main:app --host :: --port 8000 &        # IPv6
```

Each process keeps its own in-memory state (vector store, response cache, rate-limit counters). Login tokens are stateless, so either process validates them.

## Endpoints

### `GET /health`

Public liveness check. Without a valid `X-Admin-Key` it returns a minimal view:

```json
{
  "status": "ok",
  "llm_provider": "claude",
  "hybrid_search": true,
  "uptime_seconds": 86400
}
```

With a valid `X-Admin-Key` (or when `ADMIN_API_KEY` is empty) it also returns the details used by the admin dashboard: `indexed_chunks`, `unique_sources`, `doc_types`, `llm_model`, `last_indexed`, `queries_since_restart`, `gaps_total`, `gaps_recent`, `feedback` (`total`/`positive`/`negative`), `activity` (response time, top queries, hourly counts, token usage and cost, per-user token history) and `top_doc`.

---

### `POST /chat`

Complete response (non-streaming). Waits for the full response before replying.

**Rate limit**: 20 requests/hour per client IP.

**Request:**
```json
{
  "question": "How do I apply for an identity card?",
  "history": [
    {"role": "user", "content": "Where is the registry office?"},
    {"role": "assistant", "content": "The registry office is located at..."}
  ]
}
```

- `question`: string 1–1000 characters
- `history`: optional, max 6 messages (3 turns); each `{role: "user"|"assistant", content}`, with `content` truncated to 2000 characters
- **Authentication**: with `AUTH_ENABLED=true`, `Authorization: Bearer <token>` (from `POST /auth/login`) is required; otherwise `401`
- Personal data recognizable in `question` and in the user messages of `history` is masked before reaching the model and the logs (`api/pii.py`); assistant messages are passed unchanged

**Response:**
```json
{
  "answer": "An identity card can be requested at the Registry Office...",
  "sources": [
    {"title": "Electronic Identity Card", "url": "https://...", "score": 0.87}
  ],
  "language": "en",
  "rid": "a1b2c3d4e5f6"
}
```

In `sources`, `url` is empty for documents on `MIGRATED_DOMAINS` (title only). `rid` identifies the question in `usage.jsonl`, `gaps.jsonl`, `feedback.jsonl` and the API log; pass it to `POST /feedback` so that the question can later be deleted everywhere with `POST /usage/forget`.

Messages with no question in them (greetings, thanks, compliments, goodbyes, insults: "ciao", "grazie", "bravo", "sei inutile") get a fixed reply with no retrieval and no model call, and are not logged as content gaps (`api/smalltalk.py`). A message counts as such only if it is short and made only of those words plus a few fillers: a single content word ("grazie, e per la TARI?") sends it down the normal path.

---

### `POST /chat/stream`

Streaming response via **Server-Sent Events (SSE)**. This is the endpoint the widget uses.

**Rate limit, request and authentication**: same as `/chat`.

**Event stream** (each event is followed by a blank line):
```
data: {"token": "An "}
data: {"token": "identity "}
data: {"token": "card "}
...
data: {"sources": [{"title": "...", "url": "...", "score": 0.87}], "rid": "a1b2c3d4e5f6"}
data: {"done": true}
```

While waiting, the server sends `: keep-alive` comments every 15 seconds. If generation fails mid-stream:
```
data: {"error": "Errore durante la generazione"}
data: {"done": true}
```

Errors before the stream starts (400/401/422/429/500) are returned as normal JSON responses. When nothing relevant is found, the stream contains a single fallback token ("Non ho trovato informazioni specifiche…", plus the suggested office) and an empty `sources` list.

---

### `POST /feedback`

Saves a 👍/👎 rating. With `AUTH_ENABLED=true` it requires `Authorization: Bearer <token>`.

**Rate limit**: 60 requests/hour per IP.

**Request:**
```json
{
  "question": "How do I apply for an identity card?",
  "answer": "An identity card can be requested...",
  "rating": -1,
  "comment": "The opening hours are out of date",
  "urls": ["https://www.example.org/registry-office"],
  "rid": "a1b2c3d4e5f6"
}
```

- `rating`: `1` (positive) or `-1` (negative)
- `comment` (optional, max 2000 characters) and `urls` (optional; http/https only, max 5 kept) can also be added later with `POST /feedback/detail`
- `rid` (optional): the question's id from `/chat` or `/chat/stream`; stored only if it is 12 hex characters

**Response:** `{"ok": true, "id": "3f9c2a1b7d4e"}`

Saved in `data/feedback.jsonl`: the question (personal data masked, first 200 characters), the first 100 characters of the answer and, with login enabled, the user's id and name. A 👎 with a comment or links sends an email to `FEEDBACK_NOTIFY_EMAIL` (if SMTP is configured).

---

### `GET /stats` *(admin)*

Vector store statistics.

```
Headers: X-Admin-Key: <ADMIN_API_KEY>
```

```json
{"collection": "numpy_store", "total_chunks": 5420, "unique_sources": 1830, "doc_types": {"html": 4100, "pdf": 1320}}
```

---

### `GET /gaps?limit=50` *(admin)*

Latest questions with no retrieved content (`chunks: 0`) or only weakly relevant content (`weak: true`): content gaps to fill. Returns the last `limit` entries, oldest first.

```json
{
  "gaps": [
    {"ts": "2026-04-08T10:23:00+02:00", "query": "library hours", "chunks": 0, "weak": false, "rid": "a1b2c3d4e5f6"}
  ],
  "total": 42
}
```

---

### `GET /feedback/negative?limit=200` *(admin)*

Negative feedback (👎) not yet marked as resolved, with the author, comment and links reported by the user. Used by the admin view of the dashboard.

```json
{
  "items": [
    {
      "id": "3f9c2a1b7d4e",
      "ts": "2026-04-22T18:03:50+02:00",
      "question": "How do I book an appointment?",
      "answer_preview": "You can book...",
      "user": "Mario Rossi",
      "comment": "The booking link is missing",
      "urls": ["https://www.example.org/appointments"],
      "resolved": false
    }
  ],
  "total_negative": 8,
  "total": 30
}
```

`total_negative` counts the open (unresolved) negatives; `total` counts all feedback entries.

---

### `GET /feedback/list?limit=100` *(admin)*

Full list of received feedback (all ratings) with positive/negative count.

```json
{
  "feedback": [...],
  "total": 156,
  "positive": 134,
  "negative": 22
}
```

---

### `POST /usage/forget` *(admin)*

Deletes questions on request, without restarting the API: removes them from `usage.jsonl`, `gaps.jsonl` and `feedback.jsonl` (with the "resolved" marks of the deleted feedback), then from this process's memory (the most-frequent-questions counter saved in `stats.json`, and the response cache). The API log only contains the question id, never its text.

```json
{"uid": "eb3903878af10b34", "last": 1, "dry_run": true}
```

- Selection: `rids` (list of question ids), or `uid` with `last` (the user's last N questions), `all` (all their questions and feedback) or `ts` (exact `usage.jsonl` timestamps)
- `dry_run`: only returns what would be deleted
- `memory_only`: cleans this process's memory and leaves the files alone. Production runs two processes (IPv4 and IPv6) with separate memory: `scripts/forget.py` calls the others with `memory_only` first, then one without it
- Entries written before 5 October 2026 have no `rid`: they are matched by user, text and time (gap within 5 minutes, feedback after the question). Questions from anonymous visitors are only in `gaps.jsonl` and can only be found by `rid`

**Response:** the questions found (`ts`, `rid`, `q`), how many entries were (or would be) removed from each file (`usage`, `gaps`, `feedback`, `resolved`), how many were removed from memory (`memory`) and `files_changed`.

Use it through `scripts/forget.py` (see [Operations](operations.md#deleting-questions-on-request)).

---

### Other endpoints

| Endpoint | Access | Description |
|---|---|---|
| `POST /auth/login` | public (10/min per IP) | Body `{"email", "password"}` → `{"token", "name", "expires_in"}`; `400` if `AUTH_ENABLED=false`, `401` on wrong credentials |
| `POST /auth/forgot` | public (5/hour per IP) | Body `{"email"}`: sets a new random password and emails it (only if SMTP is configured; the password is changed anyway); always the same generic reply; `400` if `AUTH_ENABLED=false` |
| `GET /auth/me` | user | Current user `{"uid", "name"}`; `401` if the token is missing or expired |
| `POST /feedback/detail` | user (60/hour per IP) | Body `{"id", "comment", "urls"}`: adds a comment and links to the caller's own feedback |
| `POST /feedback/resolve` | admin | Body `{"ts", "question", "note"?, "notify"?}`: marks a negative feedback as resolved and, unless `notify` is `false`, emails the reporter with the optional note |
| `GET /usage/summary` | admin | Per-user usage (messages, active days, first/last seen, including registered users who never wrote) and daily totals |
| `GET /usage/messages?limit=300` | admin | Questions typed by logged-in users, newest first |
| `GET /crawl-history` | admin | Recent crawl/indexing events read from `/var/log/chatbot-sync.log`, plus the current progress |
| `GET /docs` | public | Swagger UI (schema at `/openapi.json`) |

"user" means: with `AUTH_ENABLED=true`, `Authorization: Bearer <token>` is required; with `AUTH_ENABLED=false` the endpoint is open.

## Admin Authentication

Admin-only endpoints: `/stats`, `/gaps`, `/feedback/negative`, `/feedback/list`, `/feedback/resolve`, `/usage/summary`, `/usage/messages`, `/usage/forget`, `/crawl-history`. They require the `X-Admin-Key` header with the value of `ADMIN_API_KEY` from `.env`; a missing or wrong key returns `403`. `/health` is public but returns its detailed view only with a valid key.

If `ADMIN_API_KEY` is empty, admin authentication is disabled (development only): all admin endpoints and the detailed `/health` are open to anyone.

## User Authentication

With `AUTH_ENABLED=true`, `/chat`, `/chat/stream`, `/feedback`, `/feedback/detail` and `/auth/me` require `Authorization: Bearer <token>`, obtained from `POST /auth/login`. Tokens are stateless, signed with HMAC-SHA256 using `AUTH_SECRET`, and valid for `AUTH_TOKEN_TTL_DAYS` days (default 30). Users are stored in `data/users.json` (password hashed with scrypt) and managed with `python -m scripts.adduser`. A missing, invalid or expired token returns `401`.

## CORS

The CORS middleware uses `API_CORS_ORIGINS` (comma-separated, no spaces; default `http://localhost:8000`). In production, set the exact site domain. Allowed methods: `GET`, `POST`, `OPTIONS`; allowed request headers: `Content-Type` and `Authorization`; credentials (cookies) are not allowed. `X-Admin-Key` is not an allowed header, so browsers can call the admin endpoints only from the API's own origin (e.g. the dashboard served at `/widget/dashboard.html`).

## Shutdown

`api/main.py` registers a SIGTERM handler that only logs the signal. It replaces uvicorn's own handler, so SIGTERM no longer stops uvicorn and the 2-second wait meant to follow it never runs: `service chatbot stop` therefore ends the service with SIGKILL (the daemon after 10 seconds, then the uvicorn processes).

## Static Files

The whole `widget/` directory is served by FastAPI under `/widget/` (`chatbot-widget.js`, `site-footer.js`, the pages, logos and icons). Every response has `Cache-Control: no-cache`, so browsers revalidate each time (304 if unchanged) and updates show up without a forced reload.

---

← [How it works](how-it-works.md) · [Documentation index](README.md) · [Frontend Widget](widget.md) →
