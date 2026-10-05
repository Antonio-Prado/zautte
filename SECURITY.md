# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| latest (`main`) | ✅ |

## Reporting a vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Send a report to: **antonio@prado.it**

Include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (optional)

You will receive an acknowledgement within 48 hours and a status update within 7 days.

## Security design notes

- **Stateless backend**: conversations are kept by the widget, not on the server; single questions are logged after personal-data masking, with retention periods and deletion on request (see [Privacy and Security](docs/privacy-and-security.md))
- **Rate limiting**: `/chat` endpoints are limited to 20 req/hour per IP via `slowapi`
- **Admin endpoints** (`/stats`, `/gaps`, `/feedback/*`, `/usage/*`, `/crawl-history`) require the `X-Admin-Key` header; set `ADMIN_API_KEY` in `.env`
- **CORS**: restrict `API_CORS_ORIGINS` to your own domain in production
- **Secrets** (`ANTHROPIC_API_KEY`, `ADMIN_API_KEY`, `AUTH_SECRET`): only in `.env`, never in code or version control; `chmod 600 .env` so that only its owner (and root) can read it, backups of `.env` included
