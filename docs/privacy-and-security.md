<sub>[Zautte](../README.md) › [Documentation](README.md) › Privacy and Security</sub>

# Privacy and Security

## GDPR

- **Conversation history** is kept client-side by the widget; the backend does not store conversations.
- **Personal data masking** (`api/pii.py`): tax codes, IBANs, payment card numbers, email addresses (except institutional domains in `PII_KEEP_EMAIL_DOMAINS`) and Italian phone numbers are replaced with a placeholder before the question (and the user's earlier messages in the conversation) reaches the LLM, the logs and the feedback archive. Feedback comments are stored as typed. Free-form data such as names cannot be detected reliably, so the widget still asks users not to enter personal data. Enabled by default (`PII_REDACTION=false` disables it).
- **What is stored**: `gaps.jsonl` (question text, no user), `feedback.jsonl` (rating, question, first 100 characters of the answer, comment and links, author for pilot users), `usage.jsonl` (question text, user id and metrics, pilot users only), `stats.json` (counters, the 100 most frequent questions, token history with user id; not covered by `purge_logs.py`), `resolved_negative.json` (resolution marks: feedback timestamp and question, the admin's note and the email address the notice was sent to) and `users.json` (name, email and password hash of pilot users). Each question gets a 12-character id (`rid`) stored with it in every file; the application log `/var/log/chatbot.log` records only that id, the length and the number of passages found, never the text (until 5 October 2026 it recorded the first 60 characters of each masked question).
- **Messages with no question** (greetings, thanks, compliments, insults) get a fixed reply: they do not reach the model and are not logged as content gaps or among the most frequent questions (`api/smalltalk.py`); they stay in `usage.jsonl` like any other message.
- **Retention**: `scripts/purge_logs.py` deletes entries older than the `RETENTION_*_DAYS` settings (0 = keep forever), removes the question text from `usage.jsonl` earlier and drops the "resolved" marks of deleted feedback. `--dry-run` shows what it would delete. Run it daily via cron.
- **Deletion on request**: `scripts/forget.py` (admin, `POST /usage/forget`) deletes a user's last N questions, all of them, or single questions by id from every file above and from the memory of the running API, without a restart (see [Operations](operations.md#deleting-questions-on-request)). Notification emails already sent and the provider's own copy (see below) are out of its reach.
- **Privacy notice**: a draft is in `docs/informativa-privacy-zautte.md`; it must be approved by the DPO before publication. Link it from the widget with `privacyUrl`.
- **Where data goes**: with `LLM_PROVIDER=ollama` nothing leaves the server; with `claude` or `bedrock` the masked question, the last messages of the conversation and the retrieved passages are sent to the provider (see below). Embeddings are always computed locally by Ollama. The pages in `widget/` also load the latest release and commit from the public GitHub API in the visitor's browser.

## AI transparency

- The widget states at the first interaction that the user is talking to an AI system (EU AI Act, Art. 50), shows an "IA" badge in the header and an "AI-generated answers" note in the footer.
- `widget/come-funziona.html` explains in plain language how answers are produced, their limits and human oversight (Italian Law 132/2025, Art. 14). Link it from the widget with `infoUrl`. The page describes the San Benedetto del Tronto deployment with Claude: adapt the organization and the model provider before reusing it.

## Using Claude in a public administration

- **Direct Anthropic API** (`LLM_PROVIDER=claude`): EEA customers contract with Anthropic Ireland; the Data Processing Addendum (with EU Standard Contractual Clauses) is part of the Commercial Terms; API data is not used for training and is deleted within 30 days (up to 2 years for content flagged for usage-policy violations). Processing can take place outside the EU (no EU data-residency option as of October 2026), and the service is not in the Italian ACN catalogue of qualified cloud services.
- **Claude on AWS Bedrock** (`LLM_PROVIDER=bedrock`, requires `pip install "anthropic[bedrock]"`): with the `eu.` inference profile called from `eu-south-1` (Milan) requests are processed only in EU regions; AWS does not store prompts or completions and does not share them with Anthropic. AWS machine-learning services, Bedrock included, are ACN-qualified (QC2).
- In both cases the municipality is the data controller: it needs a DPIA, a record of processing activities and a privacy notice, and must purchase the service in line with public procurement rules.

## Rate Limiting

Implemented with slowapi (`api/limiter.py`), per client IP:

- `/chat` and `/chat/stream`: **20 requests/hour**
- `/feedback` and `/feedback/detail`: **60 requests/hour**
- `/auth/login`: **10/minute**; `/auth/forgot`: **5/hour**

Counters are kept in memory by each uvicorn process and reset on every restart (including the one at the end of each sync). Excess requests get `429`.

## Protected Admin Endpoints

`/stats`, `/gaps`, `/feedback/list`, `/feedback/negative`, `/feedback/resolve`, `/usage/summary`, `/usage/messages` and `/crawl-history` require the `X-Admin-Key` header, and `/health` returns its detailed fields only with it. Set `ADMIN_API_KEY` in `.env` in production: if it is empty, admin authentication is disabled. `/docs` and `/openapi.json` are public.

## CORS

Configured to accept requests only from origins in `API_CORS_ORIGINS`. In production, set the exact site domain.

## Reverse Proxy (recommended)

In production put nginx or caddy with TLS in front of the backend; the backend does not handle HTTPS. `start.sh` listens on all interfaces (`0.0.0.0` and `::`, port 8000), so restrict port 8000 with the firewall. `/chat/stream` already sends `X-Accel-Buffering: no`, which turns off nginx buffering for SSE: do not override it (or set `proxy_buffering off`). For the per-IP rate limits to see the real client, uvicorn must trust the proxy's `X-Forwarded-For`: set `FORWARDED_ALLOW_IPS` to the proxy address.

---

← [Operations](operations.md) · [Documentation index](README.md) · [Accessibility Testing](accessibility-testing.md) →
