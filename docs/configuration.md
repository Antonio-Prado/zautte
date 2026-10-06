<sub>[Zautte](../README.md) › [Documentation](README.md) › Configuration</sub>

# Configuration

Configuration lives in `config/settings.py`, which loads variables from `.env` via `python-dotenv` (real environment variables take precedence). Site-specific data lives in `config/*.json`; the retrieval thresholds and fusion weights (`MIN_SIMILARITY`, `RETRIEVAL_CONFIDENCE`, `VECTOR_WEIGHT`/`BM25_WEIGHT`, `PDF_PENALTY`) are constants in `api/rag.py`, calibrated for the embedding model in use.

## `.env` File

```ini
# Site to index (required)
SITE_URL=https://www.your-site.com
SITE_NAME=Your-organization
# Domains allowed during the crawl (comma-separated; with an empty list only the start URLs are fetched)
CRAWL_ALLOWED_DOMAINS=www.your-site.com
# Domains whose links are dead after a migration: content stays indexed, links are hidden
# (the default lists the San Benedetto del Tronto ones; leave empty on other sites)
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
SMTP_STARTTLS=false
# Login page URL included in the credential emails
PILOT_LOGIN_URL=
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
# Leave empty to disable admin authentication (development only)
ADMIN_API_KEY=your-secret-key-here
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
| `CRAWL_URL_ALIASES`    | `url_aliases` in `crawl_extra.json` | Hosts that serve the same files under a path prefix (the CMS attachments on `www.`, the API host and the S3 bucket): their URLs are rewritten to `canonical_host`, so each file is fetched and indexed once and attachments on hosts outside `CRAWL_ALLOWED_DOMAINS` are included |
| `MIGRATED_DOMAINS`     | San Benedetto del Tronto domains | Domains whose links are hidden from answers and sources |
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
| `RETENTION_*_DAYS`     | `90` / `365` / `180` / `365`   | Retention of question text, usage, gaps, feedback    |

Constants in `api/rag.py`, calibrated for `bge-m3`:

| Constant               | Value  | Description                                                    |
|------------------------|--------|----------------------------------------------------------------|
| `MIN_SIMILARITY`       | `0.50` | Chunks with a lower cosine similarity are discarded            |
| `RETRIEVAL_CONFIDENCE` | `0.52` | If even the best chunk is below it, the question is logged as a weak answer in `gaps.jsonl` |
| `VECTOR_WEIGHT` / `BM25_WEIGHT` | `0.6` / `0.4` | Weights of the two rankings in the hybrid search |
| `PDF_PENALTY`          | `0.04` | Subtracted from PDF chunks when re-ranking, so pages come first at similar relevance |

---

← [Installation](installation.md) · [Documentation index](README.md) · [How it works](how-it-works.md) →
