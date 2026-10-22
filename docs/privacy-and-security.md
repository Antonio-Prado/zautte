<sub>[Zautte](../README.md) › [Documentation](README.md) › Privacy and Security</sub>

# Privacy and Security

## GDPR

- **One index for everybody**: every user who can ask a question can receive an answer built from any document in the index, including those put in `data/inbox/`. Only public documents belong there; there is no per-document or per-user permission. A `source_url` that is not `http(s)` (or the default `file://` name) is replaced when an inbox document is indexed, and the widget only makes links of `http(s)` sources.
- **Conversation history**: the last 3 turns of each conversation are kept in the memory of the API (masked questions and the answers given; two hours, tied to the user, never written to disk, lost on restart); the widget only holds a conversation id. Deleting on request also clears them.
- **Personal data masking** (`api/pii.py`): tax codes, IBANs, payment card numbers, email addresses (except institutional domains in `PII_KEEP_EMAIL_DOMAINS`) and Italian and foreign phone numbers are replaced with a placeholder before the question (and the user's earlier messages in the conversation) reaches the LLM, the logs and the feedback archive. Feedback comments and the answer excerpt go through the same filters: a comment that contains personal data is replaced by a notice, and links that contain any are dropped. Free-form data such as names cannot be detected reliably, so the widget still asks users not to enter personal data. Enabled by default (`PII_REDACTION=false` disables it). Separately, `PII_BLOCK` (default on, independent of `PII_REDACTION`) refuses a question that contains personal data: no search, no model call, nothing logged, only a fixed reply asking to rephrase. Blocking also triggers on first-person introductions such as "my name is…", "I live in via … 5" or "I was born in…". It also stops a person's address at an institutional domain (`name.surname@`), while office addresses (`protocollo@`, `ufficio.tributi@`) pass.
- **What is stored**: `gaps.jsonl` (question text, no user), `feedback.jsonl` (rating, question, first 100 characters of the answer, comment and links, author for pilot users), `usage.jsonl` (question text, user id and metrics; logged-in users only, nothing is written with `AUTH_ENABLED=false`), `answers.jsonl` (the answer shown to the user, with its sources, user id and question id; logged-in users only; not masked, since the question already is and the text comes from the municipality's public content), `stats.json` (counters, the 100 most frequent questions, token history with user id; expired by the API itself after `RETENTION_STATS_DAYS`, when it starts and every six hours, not by `purge_logs.py`), `resolved_negative.json` (resolution marks: feedback timestamp and question, the admin's note and the email address the notice was sent to) and `users.json` (name, email and password hash of the users). Each question gets a 12-character id (`rid`) stored with it in every file; the application log `/var/log/chatbot.log` records only that id, the length and the number of passages found, never the text. The same file holds the web server's access log (client IP address, path and status of every request, with `token=`/`key=` values hidden), kept 90 days by default (see [Operations](operations.md)).
- **Messages with no question** (greetings, thanks, compliments, insults) get a fixed reply: they do not reach the model and are not logged as content gaps or among the most frequent questions (`api/smalltalk.py`); they stay in `usage.jsonl` like any other message.
- **Retention**: `scripts/purge_logs.py` deletes entries older than the `RETENTION_*_DAYS` settings (0 = keep forever), removes the question text from `usage.jsonl` and the answers from `answers.jsonl` after the shorter `RETENTION_USAGE_TEXT_DAYS`, and drops the "resolved" marks of deleted feedback. `--dry-run` shows what it would delete. It takes the same file lock as the API (`<name>.lock`), so lines written meanwhile are not lost. Defaults: question text in `usage.jsonl` and `answers.jsonl` 90 days (`RETENTION_USAGE_TEXT_DAYS`), `usage.jsonl` entries 365, `gaps.jsonl` 180, `feedback.jsonl` 365, `stats.json` 90 (`RETENTION_STATS_DAYS`). Run it daily via cron (the example in [Operations](operations.md#etccrontab--production-schedule) has the line, commented out until the retention periods are agreed with the DPO).
- **Deletion on request**: `scripts/forget.py` (admin, `POST /usage/forget`) deletes a user's last N questions, all of them, or single questions by id from every file above and from the memory of the running API, without a restart (see [Operations](operations.md#deleting-questions-on-request)). Notification emails already sent and the provider's own copy (see below) are out of its reach.
- **Privacy notice**: each organisation writes its own with its DPO before publication and links it from the widget with `privacyUrl`.
- **Where data goes**: with `LLM_PROVIDER=ollama` nothing leaves the server; with `claude` or `bedrock` the masked question, the last messages of the conversation and the retrieved passages are sent to the provider (see below). Embeddings are always computed locally by Ollama. The pages in `widget/` also load the latest release and commit from the public GitHub API in the visitor's browser, and, only if the installation defines `window.ZautteMatomo`, send page views and clicks on external links (not the questions) to the organisation's own Matomo server, without cookies (`widget/matomo.js`).

## Content-Security-Policy

The pages the API serves (the widget pages, the password reset page, the login page of the restricted area) carry a Content-Security-Policy (`api/csp.py`): scripts may come only from the same origin, plus the inline scripts that are in the page as served, each named by its SHA-256 (computed from the file on disk, so an installation's own pages need no change); connections go to the same origin and to `api.github.com` (the footer) and `CSP_EXTRA_HOSTS`; no plugins, no `<base>`, forms only to the same origin, and the pages cannot be framed (`FRAME_ANCESTORS`). A script injected by a flaw in how an answer is drawn is not run and cannot read the session token the browser keeps. `CSP_MODE=report-only` makes the browser only log violations, to try a change; `off` removes the header. The pages also send `Referrer-Policy: strict-origin-when-cross-origin` and a `Permissions-Policy` that turns camera, microphone, location and payment off. Add the Matomo server to `CSP_EXTRA_HOSTS`, or its script is blocked. If the widget is embedded in another site, that site's own policy applies to it.

## Hostile text in the index

Pages, PDFs and attachments are written by others, so what the model reads from them is treated as data, not as instructions:
- the instructions tell the model to ignore commands found in the documents, never to give an address, IBAN, phone number or email that is not in them, and not to draft official acts;
- a passage cannot imitate the labels and separators that frame the passages in the prompt, and titles are cut to one line;
- text a visitor cannot see is not indexed: elements hidden with `hidden`, `display:none`, tiny or transparent text, off-screen positions, `sr-only`-style classes, hidden or deleted text in DOCX and ODT, and invisible Unicode characters (zero-width, bidirectional marks, the "tag" block);
- with nothing relevant found there is no model call at all;
- the widget links only to the hosts listed in `link_hosts`: the site's own domains, the hosts named in the known facts (written by the administrator) and the hosts of the sources of the passages given to the model, never an address found inside the text of a passage; an email address at any other host is shown as text too.
None of this makes a page that really says something false trustworthy: whoever can publish on the indexed site can still make the bot repeat it.

## AI transparency

- The widget states at the first interaction that the user is talking to an AI system (EU AI Act, Art. 50), shows an "IA" badge in the header and an "AI-generated answers" note in the footer.
- `widget/come-funziona.html` explains in plain language how answers are produced, their limits and human oversight (Italian Law 132/2025, Art. 14). Link it from the widget with `infoUrl`. The page describes a deployment with Claude: adapt the organization and the model provider before reusing it.

## Using Claude in a public administration

- **Direct Anthropic API** (`LLM_PROVIDER=claude`): EEA customers contract with Anthropic Ireland; the Data Processing Addendum (with EU Standard Contractual Clauses) is part of the Commercial Terms; API data is not used for training and is deleted within 30 days (up to 2 years for content flagged for usage-policy violations). Processing can take place outside the EU (no EU data-residency option as of October 2026), and the service is not in the Italian ACN catalogue of qualified cloud services.
- **Claude on AWS Bedrock** (`LLM_PROVIDER=bedrock`, requires `pip install "anthropic[bedrock]"`): with the `eu.` inference profile called from `eu-south-1` (Milan) requests are processed only in EU regions; AWS does not store prompts or completions and does not share them with Anthropic. AWS machine-learning services, Bedrock included, are ACN-qualified (QC2).
- In both cases the municipality is the data controller: it needs a DPIA, a record of processing activities and a privacy notice, and must purchase the service in line with public procurement rules.

## Rate Limiting

Questions (`/chat` and `/chat/stream`, one counter for both): **`DAILY_QUESTION_LIMIT` per day per user** (default 20, `0` = no limit), counted on the logged-in user and not on the IP address, so colleagues behind the same office address each have their own. The day is the server's calendar day: counters restart at midnight. They live in memory (one uvicorn process) and on startup are rebuilt from `data/usage.jsonl`, so a restart does not hand out new questions. A question counts when it arrives and is given back if the answer fails. With `AUTH_ENABLED=false` the limit is per client IP and starts from zero after a restart. Over the limit: `429` with `Retry-After` (implemented in `api/quota.py`).

**Answers at once**: `LLM_MAX_PER_USER` (default 2) for each user and `LLM_MAX_CONCURRENT` (default 8) in the whole process; a request over the limit is refused with `429` ("busy") before it uses any daily quota, so one user cannot open dozens of answers in parallel.

The other limits use slowapi (`api/limiter.py`), per client address (an IPv6 client is counted by its /64 network, `api/clientkey.py`, since one connection gets 2^64 addresses), with counters in memory that reset on every restart:

- `/feedback` and `/feedback/detail`: **60 requests/hour**
- `/auth/login`: **10/minute**; `/auth/forgot`: **5/hour**; `/auth/reset`: **10/hour**

Excess requests get `429`.

**Per account** (`api/account_guard.py`): after `LOGIN_MAX_FAILURES` wrong passwords (default 10) within `LOGIN_LOCK_SECONDS` (900) an account refuses logins, with the same answer as a wrong password, until that time has passed; a right login clears the count. At most `RESET_EMAILS_PER_HOUR` (3) reset emails are sent per account, the others silently dropped. **Request size**: a body over `MAX_BODY_BYTES` (128 KB) is refused before it is read (`413`, or `400` when it is cut while streaming).

## Protected Admin Endpoints

`/stats`, `/gaps`, `/feedback/list`, `/feedback/negative`, `/feedback/resolve`, `/usage/summary`, `/usage/messages`, `/usage/forget`, `/crawl-history` and `/admin/reload` require the `X-Admin-Key` header, and `/health` returns its detailed fields only with it. Set `ADMIN_API_KEY` in `.env` in production (after `ADMIN_MAX_ATTEMPTS` wrong keys the client address is locked out for `ADMIN_LOCKOUT_SECONDS`, see [API](api.md#admin-authentication)): if it is empty, short (under 16 characters) or a placeholder, admin requests are refused (only `ADMIN_AUTH_DISABLED=true`, for development, opens them, and only when no usable key is set). `/docs` and `/openapi.json` exist only with `API_DOCS=true` (development); `/client-ip` is public and tells each caller only their own address (shown in the page footer).

## CORS

Configured to accept requests only from origins in `API_CORS_ORIGINS`. In production, set the exact site domain.

## Reverse Proxy (recommended)

In production put nginx or caddy with TLS in front of the backend; the backend does not handle HTTPS. The backend listens on `API_HOST` (local only by default; behind a proxy on another host set `API_HOST=0.0.0.0,::`), so restrict port 8000 with the firewall (template in `scripts/pf.conf.example`, see [Host Hardening](operations.md#host-hardening)). `/chat/stream` already sends `X-Accel-Buffering: no`, which turns off nginx buffering for SSE: do not override it (or set `proxy_buffering off`). For the per-IP limits to see the real client, uvicorn must trust the proxy's `X-Forwarded-For`: set `FORWARDED_ALLOW_IPS` in `.env` to the proxy address (or addresses and networks, comma-separated; default `127.0.0.1,::1`). Never use `*`: port 8000 also answers the internal network, and anyone there could forge the header.

---

← [Operations](operations.md) · [Documentation index](README.md) · [Accessibility Testing](accessibility-testing.md) →
