# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| latest (`main`) | ✅ |

## Reporting a vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Report it privately, in one of two ways:
- on GitHub: **[Report a vulnerability](https://github.com/Antonio-Prado/zautte/security/advisories/new)** (Security and quality → Advisories); only the maintainer can read it
- by email to **antonio@prado.it**

Include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (optional)

You will receive an acknowledgement within 48 hours and a status update within 7 days.

## Security design notes

- **Conversation memory**: the server keeps the last 3 turns of each conversation in memory only (two hours, tied to the user, never written to disk, lost on restart); the widget only holds a conversation id. Questions are logged after personal-data masking, with retention periods and deletion on request (see [Privacy and Security](docs/privacy-and-security.md))
- **Rate limiting**: `/chat` and `/chat/stream` allow `DAILY_QUESTION_LIMIT` questions per day per logged-in user (default 20, `api/quota.py`); feedback, login and password reset are limited per IP via `slowapi` (see [Rate Limiting](docs/privacy-and-security.md#rate-limiting))
- **Admin endpoints** (`/stats`, `/gaps`, `/feedback/negative`, `/feedback/list`, `/feedback/resolve`, `/usage/*`, `/crawl-history`, `/admin/reload`) require the `X-Admin-Key` header; `ADMIN_API_KEY` must be at least 16 characters and not a placeholder (the API refuses to start with a weak one), and after `ADMIN_MAX_ATTEMPTS` wrong keys the client address is locked out; the restricted area lives at the address in `ADMIN_PATH`, not in any public page
- **CORS**: restrict `API_CORS_ORIGINS` to your own domain in production
- **Secrets** (`ANTHROPIC_API_KEY`, `ADMIN_API_KEY`, `AUTH_SECRET`): only in `.env`, never in code or version control; `chmod 600 .env` so that only its owner (and root) can read it, backups of `.env` included
