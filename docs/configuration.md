<sub>[Zautte](../README.md) › [Documentation](README.md) › Configuration</sub>

# Configuration

Configuration lives in `config/settings.py`, which loads variables from `.env` via `python-dotenv` (real environment variables take precedence). Site-specific data lives in `config/*.json`; the retrieval thresholds and fusion weights (`MIN_SIMILARITY`, `RETRIEVAL_CONFIDENCE`, `VECTOR_WEIGHT`/`BM25_WEIGHT`, `PDF_PENALTY`) are constants in `api/rag.py`, calibrated for the embedding model in use.

## Separating your organisation's files from the code

The repository holds the generic product. Everything that belongs to one organisation (its pages, logos, footer, statistics, the changelog of the installation, the privacy notice) lives outside the repository, in a folder pointed to by `INSTANCE_DIR` in `.env`:

```
your-instance/
├── widget/        # replaces the files of the same name served at /widget/ (dashboard.html, pilot.html, come-funziona.html, site-footer.js, matomo.js, manifest.webmanifest, logos, og.png...)
├── admin_panel/   # panel.html/css/js and login.html replace the generic ones; changelog.html is appended to the panel
└── config/        # known_facts.json, synonyms.json, offices.json, crawl_extra.json (symlinked into config/)
```

A file that is missing from the folder falls back to the generic one in the repository, so you only keep what you change. The path of the restricted area is set with `ADMIN_PATH` in `.env` (empty = the area is off), so it does not appear in the code.

## `.env` File

```ini
# Site to index (required)
SITE_URL=https://www.your-site.com
SITE_NAME=Your-organization
# Domains allowed during the crawl (comma-separated; with an empty list only the start URLs are fetched)
CRAWL_ALLOWED_DOMAINS=www.your-site.com
# Domains whose links are dead after a migration: content stays indexed, links are hidden
# (comma-separated domains or path prefixes; empty = none)
MIGRATED_DOMAINS=

# LLM provider: "ollama" (local), "claude" (Anthropic API) or "bedrock" (Claude on AWS Bedrock)
LLM_PROVIDER=ollama

# Ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1:8b
OLLAMA_EMBED_MODEL=bge-m3

# Claude API (see "Using Claude in a public administration" below)
ANTHROPIC_API_KEY=
CLAUDE_MODEL=claude-sonnet-4-6
# Reasoning effort (low|medium|high) for adaptive-reasoning models
# (Sonnet 5.x, Opus 4.7 and later); ignored by claude-sonnet-4-6
LLM_EFFORT=low
# Rewrites follow-up questions into standalone queries before retrieval
QUERY_REWRITE=true

# Claude on AWS Bedrock (pip install "anthropic[bedrock]"); the "eu." inference
# profile keeps processing inside EU regions. AWS credentials come from the
# standard chain (AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY, AWS_PROFILE, ...).
BEDROCK_AWS_REGION=eu-south-1
BEDROCK_MODEL=eu.anthropic.claude-sonnet-4-6

# Personal data in questions: masked before the LLM and the logs
PII_REDACTION=true
PII_KEEP_EMAIL_DOMAINS=your-municipality.it,pec.your-municipality.it

# Retention of user logs in days (0 = keep forever), applied by scripts/purge_logs.py
RETENTION_USAGE_TEXT_DAYS=90
RETENTION_USAGE_DAYS=365
RETENTION_GAPS_DAYS=180
RETENTION_FEEDBACK_DAYS=365
RETENTION_STATS_DAYS=90

# Pilot login (email + password) and email delivery
AUTH_ENABLED=false
AUTH_SECRET=
AUTH_TOKEN_TTL_DAYS=30
# Questions per day per user on /chat and /chat/stream (0 = no limit; per IP if AUTH_ENABLED=false)
DAILY_QUESTION_LIMIT=20
SMTP_HOST=
SMTP_PORT=25
SMTP_FROM=
SMTP_USER=
SMTP_PASSWORD=
# STARTTLS on port 587, or implicit TLS (SMTP_SSL) on port 465
SMTP_STARTTLS=false
SMTP_SSL=false
# Login page URL included in the credential emails
PILOT_LOGIN_URL=
# Public address of the site, used for the links in emails (password reset).
# Empty = the origin of PILOT_LOGIN_URL; if both are empty no reset link is sent.
PUBLIC_BASE_URL=
# Address notified of every report sent from the widget (empty = none)
FEEDBACK_NOTIFY_EMAIL=

# Backend (bind address and port are set in start.sh; API_HOST/API_PORT are not used)
API_CORS_ORIGINS=https://www.your-site.com
# Proxy/load balancer whose X-Forwarded-For is trusted (addresses or networks, comma-separated; never *)
FORWARDED_ALLOW_IPS=127.0.0.1,::1
# Interactive API docs at /docs and /openapi.json (development only; default false)
API_DOCS=false

# Admin key (X-Admin-Key header) for /stats, /gaps, /feedback/{list,negative,resolve},
# /usage/{summary,messages}, /crawl-history and the detailed /health.
# With an empty key all admin requests are refused; set ADMIN_AUTH_DISABLED=true
# to open them instead (development only)
ADMIN_API_KEY=your-secret-key-here
ADMIN_AUTH_DISABLED=false
# Address of the restricted admin area, e.g. /area-riservata (empty = the area is off; the path is
# never written in the code)
ADMIN_PATH=
# Wrong admin keys allowed per address, and the lockout (429) once reached, in seconds
ADMIN_MAX_ATTEMPTS=5
ADMIN_LOCKOUT_SECONDS=900
```

## Key Parameters in `settings.py`

| Parameter              | Default                        | Description                                          |
|------------------------|--------------------------------|------------------------------------------------------|
| `SITE_URL`             | *(from .env)*                  | Root URL for crawling                                |
| `SITE_NAME`            | *(from .env)*                  | Organization name, used in the prompts and the API title |
| `CRAWL_MAX_PAGES`      | `0`                            | Maximum pages to crawl (0 = no limit)                |
| `CRAWL_DELAY_SECONDS`  | `0.3`                          | Pause between requests (~3 pages/s)                  |
| `CRAWL_ALLOWED_DOMAINS`| *(from .env)*                  | Domains allowed during crawl                         |
| `CRAWL_EXCLUDE_PATTERNS`| Lists of patterns to exclude  | URLs to ignore (admin, feeds, images, etc.), plus `exclude_patterns` from `config/crawl_extra.json` |
| `CRAWL_MAX_PATH_DEPTH` | `10`                           | Maximum URL path depth (per-domain limits in `crawl_extra.json`) |
| `CRAWL_PDF_EXCLUDE_PATTERNS` | `pdf_exclude_patterns` in `crawl_extra.json` | Pieces of PDF URLs not to download (PDF links do not go through `CRAWL_EXCLUDE_PATTERNS`, which contains `.pdf` to keep PDFs out of page crawling); production excludes two university theses published among the site's attachments (`/s3/6115/allegati/tesi-`) |
| `CRAWL_SECTION_RULES`  | `section_rules` in `crawl_extra.json` | Rules that label each page with a `section` from its host name: `[{"host_contains": "suap", "section": "suap"}]`, first match wins, `sito_principale` otherwise |
| `NEWS_SOURCES`         | `news` in `crawl_extra.json`   | Sources of the near-real-time news sync (`scripts.sync news`, see [Operations](operations.md)): `wordpress_api` (base URL of a WordPress REST API, `.../wp-json/wp/v2`), `rss` (feeds of the institutional site), `attachment_hosts` (extra hosts whose PDF/ODT/DOCX attachments are downloaded), `section` (metadata label) |
| `CRAWL_URL_ALIASES`    | `url_aliases` in `crawl_extra.json` | Hosts that serve the same files under a path prefix (the CMS attachments on `www.`, the API host and the S3 bucket): their URLs are rewritten to `canonical_host`, so each file is fetched and indexed once and attachments on hosts outside `CRAWL_ALLOWED_DOMAINS` are included |
| `MIGRATED_DOMAINS`     | empty                          | Domains whose links are hidden from answers and sources |
| `CHUNK_SIZE`           | `800`                          | Not used: chunks are paragraphs of up to 1200 characters (`MAX_PARAGRAPH_CHARS` in `indexer/chunker.py`) |
| `CHUNK_OVERLAP`        | `100`                          | Overlap when splitting long paragraphs (minimum — effective is 150) |
| `OLLAMA_EMBED_MODEL`   | `bge-m3`                       | Embedding model (1024 dim)                           |
| `EMBEDDING_DIMENSION`  | `1024`                         | Embedding vector dimension                           |
| `RETRIEVAL_TOP_K`      | `7`                            | Chunks passed to the LLM per query                   |
| `LLM_PROVIDER`         | `ollama`                       | `ollama`, `claude` or `bedrock`                      |
| `OLLAMA_MODEL`         | `llama3.1:8b`                  | Local LLM model                                      |
| `CLAUDE_MODEL`         | `claude-sonnet-4-6`            | Claude API model                                     |
| `LLM_EFFORT`           | `low`                          | Reasoning effort (`low`/`medium`/`high`) for adaptive-reasoning models (Sonnet 5.x, Opus 4.7 and later) |
| `QUERY_REWRITE`        | `true`                         | Rewrite follow-up questions as standalone queries before retrieval |
| `CLAUDE_REWRITE_MODEL` | `CLAUDE_MODEL`                 | Model used to rewrite follow-up questions (`claude` provider; with Ollama the rewrite uses `OLLAMA_MODEL`) |
| `BEDROCK_AWS_REGION`   | `eu-south-1`                   | AWS region for Bedrock (Milan)                       |
| `BEDROCK_MODEL`        | `eu.anthropic.claude-sonnet-4-6` | Bedrock EU cross-region inference profile          |
| `BEDROCK_REWRITE_MODEL`| `BEDROCK_MODEL`                | Model used to rewrite follow-up questions (`bedrock` provider) |
| `PII_REDACTION`        | `true`                         | Masks tax codes, IBANs, cards, emails, phone numbers |
| `RETENTION_*_DAYS`     | `90` / `365` / `180` / `365` / `90` | Retention of question text, usage, gaps, feedback, and of the most frequent questions and per-user token records in `stats.json` (applied by the API itself) |

Constants in `api/rag.py`, calibrated for `bge-m3`:

| Constant               | Value  | Description                                                    |
|------------------------|--------|----------------------------------------------------------------|
| `MIN_SIMILARITY`       | `0.50` | Chunks with a lower cosine similarity are discarded            |
| `RETRIEVAL_CONFIDENCE` | `0.52` | If even the best chunk is below it, the question is logged as a weak answer in `gaps.jsonl` |
| `VECTOR_WEIGHT` / `BM25_WEIGHT` | `0.6` / `0.4` | Weights of the two rankings in the hybrid search |
| `PDF_PENALTY`          | `0.04` | Subtracted from PDF chunks when re-ranking, so pages come first at similar relevance |

---

← [Installation](installation.md) · [Documentation index](README.md) · [How it works](how-it-works.md) →
