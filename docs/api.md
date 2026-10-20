<sub>[Zautte](../README.md) › [Documentation](README.md) › Backend API</sub>

# Backend API

`api/main.py` (with `api/auth.py`) — FastAPI with SSE streaming, rate limiting, user login and admin authentication.

## Starting

```sh
# Development
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

# Production (FreeBSD): `service chatbot start` runs start.sh under daemon(8);
# start.sh launches ONE single-worker process listening on IPv4 and IPv6, port 8000:
/opt/chatbot/venv/bin/python -m api.serve api.main:app --host 0.0.0.0 --host :: --port 8000 &
```

`api/serve.py` opens one socket per address and hands both to a single uvicorn server: the uvicorn command line takes only one address, and on FreeBSD `::` does not accept IPv4 connections. Without `--host` it listens only on `127.0.0.1` and `::1`: opening it to the network is an explicit choice of whoever starts it.

## Endpoints

### `GET /health`

Public liveness check. Without a valid `X-Admin-Key` it returns a minimal view:

```json
{
  "status": "ok",
  "uptime_seconds": 86400
}
```

With a valid `X-Admin-Key` (or when `ADMIN_AUTH_DISABLED=true`) it also returns the details used by the admin dashboard: `llm_provider`, `hybrid_search`, `indexed_chunks`, `unique_sources`, `doc_types`, `llm_model`, `last_indexed`, `queries_since_restart`, `gaps_total`, `gaps_recent`, `feedback` (`total`/`positive`/`negative`/`resolved`; since 10 October 2026 `negative` counts only the open negatives, the resolved ones are in `resolved` and excluded from `total`), `activity` (response time, top queries, hourly counts, token usage and cost, per-user token history) and `top_doc`.

---

### `GET /client-ip`

Public. Returns the caller's IP address as the API sees it, shown in the page footer (`site-footer.js`); behind the load balancer it is the address forwarded in `X-Forwarded-For` (trusted only from `FORWARDED_ALLOW_IPS`). Sent with `Cache-Control: no-store`.

```json
{ "ip": "203.0.113.7" }
```

---

### `POST /chat`

Complete response (non-streaming). Waits for the full response before replying.

**Daily limit**: `DAILY_QUESTION_LIMIT` questions per day (default 20) per logged-in user, shared by `/chat` and `/chat/stream`; per client IP when `AUTH_ENABLED=false`. Over the limit: `429` with `{"detail": {"error": "daily_limit", "limit": 20}}` and `Retry-After` set to the seconds until midnight; the widget shows "Hai raggiunto il limite di 20 domande al giorno…". See [Rate Limiting](privacy-and-security.md#rate-limiting).

**Request:**
```json
{
  "question": "And how much does it cost?",
  "cid": "9f1c2b7a4d3e8a10"
}
```

- `question`: string 1–1000 characters
- `cid`: optional conversation id, the one returned with the previous answer. The server keeps the last 3 turns of each conversation in memory (two hours, lost on restart, tied to the user) and uses them as context; a missing, expired or foreign id starts a new conversation. The old `history` field is still accepted but ignored: the client cannot decide what the assistant "said"
- **Authentication**: with `AUTH_ENABLED=true`, `Authorization: Bearer <token>` (from `POST /auth/login`) is required; otherwise `401`
- Personal data recognizable in `question` is masked before reaching the model, the logs and the conversation memory (`api/pii.py`)

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

In `sources`, `url` is empty for documents on `MIGRATED_DOMAINS` (title only). `rid` identifies the question in `usage.jsonl`, `answers.jsonl`, `gaps.jsonl`, `feedback.jsonl` and the API log; pass it to `POST /feedback` so that the question can later be deleted everywhere with `POST /usage/forget`.

Messages with no question in them (greetings, thanks, compliments, goodbyes, insults: "ciao", "grazie", "bravo", "sei inutile") get a fixed reply with no retrieval and no model call, and are not logged as content gaps (`api/smalltalk.py`). A message counts as such only if it is short and made only of those words plus a few fillers: a single content word ("grazie, e per la TARI?") sends it down the normal path.

---

### `POST /chat/stream`

Streaming response via **Server-Sent Events (SSE)**. This is the endpoint the widget uses.

**Daily limit, request and authentication**: same as `/chat` (one counter for both).

**Event stream** (each event is followed by a blank line):
```
data: {"token": "An "}
data: {"token": "identity "}
data: {"token": "card "}
...
data: {"sources": [{"title": "...", "url": "...", "score": 0.87}], "rid": "a1b2c3d4e5f6", "language": "en"}
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

One vote per user per answer: a second rating from the same user with the same `rid` replaces the previous one (same `id`) instead of adding a row, so repeated clicks on 👍 or a change of mind do not inflate the counters. Entries without `uid` or `rid` are kept one per row. The counters in `/health` and `/feedback/list` and the list in `/feedback/negative` apply the same rule when reading, so rows written before 12/10/2026 are counted once as well.

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

Deletes questions on request, without restarting the API: removes them from `usage.jsonl`, `answers.jsonl`, `gaps.jsonl` and `feedback.jsonl` (with the "resolved" marks of the deleted feedback), then from this process's memory (the most-frequent-questions counter saved in `stats.json`, and the response cache). The API log only contains the question id, never its text.

```json
{"uid": "eb3903878af10b34", "last": 1, "dry_run": true}
```

- Selection: `rids` (list of question ids), or `uid` with `last` (the user's last N questions), `all` (all their questions and feedback) or `ts` (exact `usage.jsonl` timestamps)
- `dry_run`: only returns what would be deleted
- The memory part assumes a single API process (`api/serve.py`): with several workers, the others keep their counters and cache until they restart
- Entries written before 5 October 2026 have no `rid`: they are matched by user, text and time (gap within 5 minutes, feedback after the question). Questions from anonymous visitors are only in `gaps.jsonl` and can only be found by `rid`

**Response:** the questions found (`ts`, `rid`, `q`), how many entries were (or would be) removed from each file (`usage`, `answers`, `gaps`, `feedback`, `resolved`), how many were removed from memory (`memory`) and `dry_run`.

Use it through `scripts/forget.py` (see [Operations](operations.md#deleting-questions-on-request)).

---

### Other endpoints

| Endpoint | Access | Description |
|---|---|---|
| `POST /auth/login` | public (10/min per IP) | Body `{"email", "password"}` → `{"token", "name", "expires_in"}`; `400` if `AUTH_ENABLED=false`, `401` on wrong credentials |
| `POST /auth/forgot` | public (5/hour per IP) | Body `{"email"}`: emails a single-use link (valid 30 minutes) to choose a new password, only if SMTP is configured and `PUBLIC_BASE_URL` (or `PILOT_LOGIN_URL`) gives the address for the link (the `Host` header of the request is never used); the mail is sent after the reply; the password is NOT changed until the link is used; always the same generic reply; `400` if `AUTH_ENABLED=false` |
| `GET /auth/reset?token=` | public | The page where the new password is chosen (at least 10 characters); `400` if the link is invalid, expired or already used |
| `POST /auth/reset` | public (10/hour per IP) | Body `{"token", "password"}`: sets the password, ends the user's older sessions and makes the link useless |
| `POST /auth/logout` | user | Revokes the token of the request (listed in `data/revoked_tokens.json` until it would have expired); the widget calls it when the user signs out |
| `GET /auth/me` | user | Current user `{"uid", "name"}`; `401` if the token is missing or expired |
| `POST /feedback/detail` | user (60/hour per IP) | Body `{"id", "comment", "urls"}`: adds a comment and links to the caller's own feedback |
| `POST /feedback/resolve` | admin | Body `{"ts", "question", "note"?, "notify"?}`: marks a negative feedback as resolved and, unless `notify` is `false`, emails the reporter with the optional note |
| `GET /usage/summary` | admin | Per-user usage (messages, active days, first/last seen, including registered users who never wrote) and daily totals |
| `GET /usage/messages?limit=300` | admin | Questions typed by logged-in users, newest first, each with the answer given (`a`, `sources`, `partial` if it was cut short; since 7 October 2026) |
| `GET /crawl-history` | admin | Recent crawl/indexing events read from `/var/log/chatbot-sync.log`, plus the current progress |
| `POST /admin/reload` | admin | Reloads the vector store from disk without restarting the API (used by `scripts.sync news`); returns the store statistics plus `reloaded` (`false` if the files were unreadable and the previous store stays in memory) |
| `GET /` | public | `302` redirect to `/widget/dashboard.html` (also answers `HEAD`) |
| `GET /client-ip` | public | `{"ip"}`: the caller's own IP address, shown in the page footer |
| `GET /docs` | public, only with `API_DOCS=true` | Swagger UI (schema at `/openapi.json`); off by default, so in production both return `404` |

"user" means: with `AUTH_ENABLED=true`, `Authorization: Bearer <token>` is required; with `AUTH_ENABLED=false` the endpoint is open.

## Admin Authentication

Admin-only endpoints: `/stats`, `/gaps`, `/feedback/negative`, `/feedback/list`, `/feedback/resolve`, `/usage/summary`, `/usage/messages`, `/usage/forget`, `/crawl-history`, `/admin/reload`. They require the `X-Admin-Key` header with the value of `ADMIN_API_KEY` from `.env`; a missing or wrong key returns `403`. `/health` is public but returns its detailed view only with a valid key.

If `ADMIN_API_KEY` is empty, every admin request is refused (the detailed `/health` stays hidden). Only with `ADMIN_AUTH_DISABLED=true` (development only) are all admin endpoints and the detailed `/health` open to anyone.

**Lockout.** A wrong key counts as a failed attempt for the client address. After `ADMIN_MAX_ATTEMPTS` (default 5) within `ADMIN_LOCKOUT_SECONDS` (default 900) that address gets `429` with `Retry-After`, even with the right key, on every admin endpoint and on `/health`. Counts are kept in memory (they restart with the service); localhost, used by the maintenance scripts, is never locked.

**Restricted area.** With `ADMIN_PATH` set (for example `/area-riservata`), `GET <ADMIN_PATH>` serves a page that only asks for the key, and `GET <ADMIN_PATH>/dati` (admin key required) returns `{css, html, js}`: the panel, its script and its styles, which are in no public page. Without `ADMIN_PATH` the area is off. Unknown paths and methods answer with a plain HTML `404`/`405` page.

## User Authentication

With `AUTH_ENABLED=true`, `/chat`, `/chat/stream`, `/feedback`, `/feedback/detail` and `/auth/me` require `Authorization: Bearer <token>`, obtained from `POST /auth/login`. Tokens are signed with HMAC-SHA256 using `AUTH_SECRET` and valid for `AUTH_TOKEN_TTL_DAYS` days (default 7). A token stops working earlier after `POST /auth/logout`, when its user is removed from `users.json`, or when that user's password changes (`pw_changed` in `users.json`, set by the password reset and by `adduser` on an existing user). Users are stored in `data/users.json` (password hashed with scrypt) and managed with `python -m scripts.adduser`. A missing, invalid or expired token returns `401`.

## CORS

The CORS middleware uses `API_CORS_ORIGINS` (comma-separated, no spaces; default `http://localhost:8000`). In production, set the exact site domain. Allowed methods: `GET`, `POST`, `OPTIONS`; allowed request headers: `Content-Type` and `Authorization`; credentials (cookies) are not allowed. `X-Admin-Key` is not an allowed header, so browsers can call the admin endpoints only from the API's own origin (e.g. the dashboard served at `/widget/dashboard.html`).

## Shutdown

`api/main.py` registers a SIGTERM handler that only logs the signal. It replaces uvicorn's own handler, so SIGTERM no longer stops uvicorn and the 2-second wait meant to follow it never runs: `service chatbot stop` therefore ends the service with SIGKILL (the daemon after 10 seconds, then the uvicorn processes).

## Static Files

The whole `widget/` directory is served by FastAPI under `/widget/` (`chatbot-widget.js`, `site-footer.js`, the pages, logos and icons). Every response has `Cache-Control: no-cache`, so browsers revalidate each time (304 if unchanged) and updates show up without a forced reload.

---

← [How it works](how-it-works.md) · [Documentation index](README.md) · [Frontend Widget](widget.md) →
