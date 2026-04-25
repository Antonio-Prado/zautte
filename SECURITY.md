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

- **Stateless backend**: no conversation data is stored server-side
- **No personal data**: gap log records only query text (truncated to 200 chars) + timestamp; feedback records only rating and truncated previews — no session IDs or IP addresses
- **Rate limiting**: `/chat` endpoints are limited to 20 req/hour per IP via `slowapi`
- **Admin endpoints** (`/stats`, `/gaps`, `/feedback/list`) require `X-Admin-Key` header; set `ADMIN_API_KEY` in `.env`
- **CORS**: restrict `API_CORS_ORIGINS` to your own domain in production
- **LLM API key**: store `ANTHROPIC_API_KEY` only in `.env`, never in code or version control
