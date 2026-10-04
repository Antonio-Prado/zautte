<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/zautte-logo-dark.svg">
    <img src="widget/zautte-logo.svg" alt="Zautte" width="320">
  </picture>
</p>

# Zautte — Technical Documentation

Zautte is a RAG (Retrieval-Augmented Generation) virtual assistant for any website. It answers user questions based exclusively on the indexed site's content, with multilingual support (Italian/English) and privacy-by-design features for GDPR compliance (personal-data masking, log retention periods, optional fully local LLM).

---

## Table of Contents

1. [Architecture](#architecture)
2. [Technology Stack](#technology-stack)
3. [Repository Structure](#repository-structure)
4. [Installation](#installation)
5. [Configuration](#configuration)
6. [RAG Pipeline](#rag-pipeline)
7. [Crawler](#crawler)
8. [Indexer](#indexer)
9. [Vector Store](#vector-store)
10. [Backend API](#backend-api)
11. [Frontend Widget](#frontend-widget)
12. [Periodic Operations](#periodic-operations)
13. [System Service (FreeBSD)](#system-service-freebsd)
14. [Monitoring and Evaluation](#monitoring-and-evaluation)
15. [Privacy and Security](#privacy-and-security)
16. [Troubleshooting](#troubleshooting)

---

## Architecture

```
                    ┌──────────────────────────────────────────┐
                    │           Site to be indexed             │
                    │        your-site.com  (HTML + PDF)       │
                    └──────────────┬───────────────────────────┘
                                   │ crawl
                                   ▼
                    ┌──────────────────────────────────────────┐
                    │             crawler/                     │
                    │  Downloads pages, extracts text/metadata │
                    │  Mode: full | incremental                │
                    └──────────────┬───────────────────────────┘
                                   │ index.json
                                   ▼
                    ┌──────────────────────────────────────────┐
                    │             indexer/                     │
                    │  Semantic chunking → Embedding (Ollama)  │
                    │  Upsert into vector store (numpy)        │
                    └──────────────┬───────────────────────────┘
                                   │ data/vectorstore/
                                   ▼
    User ─── JS Widget ─── api/main.py (FastAPI)
                                   │
                              api/rag.py
                         ┌─────────┴──────────┐
                    Hybrid Search        LLM (Ollama / Claude API /
                  (cosine + BM25,        Claude on AWS Bedrock)
                 query embedded by       Streaming response
                      Ollama)
```

The API keeps no conversation state: turn history is managed client-side by the widget and sent with every request (max 3 turns = 6 messages), and login tokens are stateless. Single questions are still logged, after personal-data masking, in `data/usage.jsonl` (logged-in users only), `data/gaps.jsonl` and `data/feedback.jsonl`, subject to the `RETENTION_*` periods; `data/stats.json` also keeps the 100 most frequent questions and the API log the first 60–120 characters of each, outside those periods (see [Privacy and Security](#privacy-and-security)).

---

## Technology Stack

| Component         | Technology                                           |
|-------------------|------------------------------------------------------|
| OS                | FreeBSD 15 (production: 15.1-RELEASE)                |
| Python            | 3.11+                                                |
| Web scraping      | httpx + BeautifulSoup4/lxml                          |
| PDF parsing       | pypdf (pure Python, no compilation)                  |
| Other documents   | python-docx (DOCX files in the inbox)                |
| Chunking          | langchain-text-splitters                             |
| Embedding         | Ollama (`bge-m3`, 1024 dim, multilingual)            |
| Vector store      | Custom in-memory store on numpy (no external database) |
| Keyword search    | rank-bm25 (BM25Okapi)                                |
| LLM               | Local Ollama (`llama3.1:8b` by default) or Claude via the Anthropic API or AWS Bedrock (`anthropic` SDK; production: Claude Sonnet 4.6) |
| Backend           | FastAPI + uvicorn                                    |
| Configuration     | python-dotenv (`.env`)                               |
| Rate limiting     | slowapi                                              |
| Frontend          | Vanilla JS/CSS (no external libraries)               |
| Supervisor        | FreeBSD rc.d + daemon(8), cron watchdog              |
| Log rotation      | newsyslog                                            |

---

## Repository Structure

```
zautte/                       # deployed as /opt/chatbot
├── .env.example              # Environment variable template
├── requirements.txt          # Python dependencies
├── pyproject.toml            # ruff configuration (Python 3.11)
├── start.sh                  # Launcher run by the rc.d service (uvicorn on 0.0.0.0 and ::, port 8000)
├── LICENSE, CONTRIBUTING.md, SECURITY.md
├── .github/                  # CI (ruff lint), CodeQL, Dependabot, issue/PR templates
│
├── config/
│   ├── settings.py           # Centralized config (loads .env)
│   ├── known_facts.json      # Curated facts injected for specific topics
│   ├── synonyms.json         # Query expansion synonyms
│   ├── offices.json          # Office suggested when the question matches its keywords
│   └── crawl_extra.json      # Site-specific crawl overrides: extra seed URLs, exclude patterns, per-domain path depth
│
├── crawler/
│   ├── crawler.py            # Async httpx crawler
│   └── state.py              # Crawl state for incremental mode
│
├── indexer/
│   ├── chunker.py            # Semantic paragraph chunking
│   ├── embedder.py           # Embedding generation via Ollama
│   ├── indexer.py            # Orchestrator: crawler output → vector store
│   ├── pdf_extractor.py      # Text extraction from PDF (pypdf)
│   └── vector_store.py       # numpy store: cosine search + BM25 hybrid
│
├── api/
│   ├── main.py               # FastAPI: endpoints, rate limiting, admin auth, static /widget
│   ├── rag.py                # RAG pipeline: rewrite → retrieve → rerank → LLM
│   ├── auth.py               # User login (pilot), stateless HMAC tokens
│   ├── pii.py                # Masks personal data in questions before LLM and logs
│   ├── feedback_store.py     # Feedback archive (votes, comments, links)
│   ├── mailer.py             # Email notifications (credentials, reports)
│   └── limiter.py            # Rate limiting (slowapi)
│
├── widget/                   # served by the API under /widget/
│   ├── chatbot-widget.js     # Chat widget (self-contained JS/CSS)
│   ├── embed-snippet.html    # Snippet to paste into the site
│   ├── dashboard.html        # Login + chat for pilot users; admin panel (#admin)
│   ├── come-funziona.html    # Public "how it works" page (AI transparency)
│   ├── pilot.html            # Pilot landing page
│   ├── site-footer.js        # Footer shared by the pages (latest release and commit, "Powered by")
│   ├── zautte-logo.svg       # Zautte logo (also used at the top of this README)
│   ├── zautte-icon.svg       # "ZA" icon in the chat header
│   ├── zautte-favicon.svg    # Favicon of the pages
│   ├── zautte-icon-180.png   # apple-touch-icon
│   └── logo.png              # "Powered by" logo in the footer (not versioned: provide your own)
│
├── docs/
│   ├── informativa-privacy-zautte.md  # Draft privacy notice (to be approved by the DPO)
│   └── zautte-logo-dark.svg  # White logo for GitHub's dark theme
│
├── scripts/
│   ├── sync.py               # Orchestrator: crawl + indexing (full, incremental, inbox, full-index, reembed)
│   ├── inbox_indexer.py      # Indexing of manually uploaded documents
│   ├── reembed.py            # Re-embeds the whole store with another model, without downtime
│   ├── eval.py               # RAG quality evaluation
│   ├── purge_logs.py         # Applies retention periods to user logs (daily cron)
│   ├── adduser.py            # Creates/updates/removes pilot users
│   ├── feedback_open.py      # Lists open negative feedback
│   ├── cleanup_index.py      # Purges crawl-cache entries now excluded by the crawler filters
│   ├── full_sync.sh          # Monthly full sync (zero downtime, sync lock)
│   ├── incremental_sync.sh   # Weekly incremental sync (sync lock)
│   ├── setup_freebsd.sh      # Initial setup on FreeBSD
│   ├── chatbot_rcd           # rc.d script for the service
│   ├── cron_setup.sh         # Installs the cron jobs (run as root)
│   ├── backup_vectorstore.sh # Daily vector store backup
│   ├── watchdog.sh           # Watchdog: restarts the API if it is down or unresponsive
│   └── newsyslog-chatbot.conf # Log rotation configuration
│
└── data/                     # Auto-generated (do not commit)
    ├── crawl_cache/          # Page cache (pages/), index.json, crawl_state.json
    ├── documents/            # Downloaded PDFs
    ├── vectorstore/          # Embeddings + metadata (numpy)
    ├── inbox/                # Documents to index manually (processed/, errors/)
    ├── backups/              # Compressed vector store backups
    ├── gaps.jsonl            # Unanswered or weakly answered queries (content gaps)
    ├── feedback.jsonl        # User feedback (thumbs up/down, comments, links)
    ├── resolved_negative.json # Negative feedback marked as resolved
    ├── usage.jsonl           # Per-user usage (pilot users only)
    ├── stats.json            # Query counters, the 100 most frequent questions, response times, token and cost history
    └── users.json            # Pilot users (password stored as scrypt hash)
```

---

## Installation

### Prerequisites

- FreeBSD 15 (production runs 15.1-RELEASE; 14.x should also work)
- Python 3.11+
- Build tools: PyPI has no binary wheels for FreeBSD, so `pip` compiles numpy, lxml,
  pydantic-core, jiter and the other native packages. Install `rust`, `ninja`, `pkgconf`,
  `libxml2` and `libxslt` first (`setup_freebsd.sh` does it). Without a system `ninja`, pip
  builds ninja and CMake from source to compile numpy, which takes a long time. `orjson`
  3.11.9 and later need Rust 1.95 or newer: with an older Rust, install with the constraint
  `orjson<=3.11.8`.
- [Ollama](https://ollama.com) installed and running (`ollama serve`): it computes the embeddings even when the answers come from Claude
- Ollama models downloaded:

```sh
ollama pull bge-m3              # embedding (1024 dim, multilingual): always needed
ollama pull llama3.1:8b         # local LLM: only with LLM_PROVIDER=ollama
```

### Automated Setup

```sh
# Clone the repository
git clone <repo_url> /opt/chatbot
cd /opt/chatbot

# Run the setup script (as root)
sh scripts/setup_freebsd.sh
```

The script installs the system packages (`python311`, git, curl, wget and the build tools above), creates the virtualenv, installs `requirements.txt` (with `orjson<=3.11.8` when Rust is older than 1.95), creates the `data/` directories and, if Ollama is already installed, pulls `bge-m3` and `llama3.1:8b`. Ollama itself must be installed separately.

### Manual Setup

```sh
cd /opt/chatbot

# Create and activate the virtualenv
python3.11 -m venv venv
. venv/bin/activate

# Install dependencies
pip install -r requirements.txt   # includes rank-bm25 for BM25 hybrid search

# Configure the environment
cp .env.example .env
# Edit .env with your values

# Create data directories
mkdir -p data/vectorstore data/documents data/crawl_cache data/inbox
```

### First Indexing

```sh
# 1. Full site crawl (no page limit by default: a large site takes hours)
venv/bin/python -m crawler.crawler

# 2. Generate embeddings and populate the vector store
venv/bin/python -m indexer.indexer

# Or in a single command (crawl + index + inbox):
venv/bin/python -m scripts.sync full
```

Every `scripts.sync` mode ends by killing the running `uvicorn api.main:app` processes, so that the API reloads the updated vector store: in production the watchdog starts the service again (see [Watchdog](#watchdog)).

### Starting the Backend

```sh
# Development (with auto-reload)
venv/bin/python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

# Production (via rc.d — see dedicated section)
service chatbot start
```

---

## Configuration

Configuration lives in `config/settings.py`, which loads variables from `.env` via `python-dotenv` (real environment variables take precedence). Site-specific data lives in `config/*.json`; the retrieval thresholds and fusion weights (`MIN_SIMILARITY`, `RETRIEVAL_CONFIDENCE`, `VECTOR_WEIGHT`/`BM25_WEIGHT`) are constants in `api/rag.py`, calibrated for the embedding model in use.

### `.env` File

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

# Admin key (X-Admin-Key header) for /stats, /gaps, /feedback/{list,negative,resolve},
# /usage/{summary,messages}, /crawl-history and the detailed /health.
# Leave empty to disable admin authentication (development only)
ADMIN_API_KEY=your-secret-key-here
```

> **Known limitation**: the rc.d script (`scripts/chatbot_rcd`) exports `.env` with `export $(grep -v '^#' .env | xargs)`, which splits values on spaces and does not strip comments at the end of a line. Keep every comment on its own line, and avoid spaces in values: with the rc.d service, `SITE_NAME=Comune di Esempio` is seen as `SITE_NAME=Comune` (`python-dotenv` does not override variables that are already set).

### Key Parameters in `settings.py`

| Parameter              | Default                        | Description                                          |
|------------------------|--------------------------------|------------------------------------------------------|
| `SITE_URL`             | *(from .env)*                  | Root URL for crawling                                |
| `SITE_NAME`            | *(from .env)*                  | Organization name, used in the prompts and the API title |
| `CRAWL_MAX_PAGES`      | `0`                            | Maximum pages to crawl (0 = no limit)                |
| `CRAWL_DELAY_SECONDS`  | `0.3`                          | Pause between requests (~3 pages/s)                  |
| `CRAWL_ALLOWED_DOMAINS`| *(from .env)*                  | Domains allowed during crawl                         |
| `CRAWL_EXCLUDE_PATTERNS`| Lists of patterns to exclude  | URLs to ignore (admin, feeds, images, etc.), plus `exclude_patterns` from `config/crawl_extra.json` |
| `CRAWL_MAX_PATH_DEPTH` | `10`                           | Maximum URL path depth (per-domain limits in `crawl_extra.json`) |
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
| `MIN_SIMILARITY`       | `0.45` | Chunks with a lower cosine similarity are discarded            |
| `RETRIEVAL_CONFIDENCE` | `0.52` | If even the best chunk is below it, the question is logged as a weak answer in `gaps.jsonl` |
| `VECTOR_WEIGHT` / `BM25_WEIGHT` | `0.6` / `0.4` | Weights of the two rankings in the hybrid search |

---

## RAG Pipeline

Each question flows through this pipeline (`api/main.py`, then `answer()` in `api/rag.py`):

```
User question
     │
     ▼
1. redact()               — masks tax codes, IBANs, cards, emails, phones in the question and in the user turns of the history (api/pii.py)
     │
     ▼
2. cache lookup           — /chat (non-streaming) without history only
     │
     ▼
3. detect_language()      — Italian vs English (keyword heuristic)
     │
     ▼
4. contextualize_query()  — follow-ups only: the LLM rewrites the question as a standalone one
     │
     ▼
5. retrieve_context()
     ├─ known facts          — curated texts from config/known_facts.json (score 1.0)
     ├─ expand_query()       — appends synonyms from config/synonyms.json
     ├─ embed_query()        — vectorizes the expanded query (Ollama bge-m3)
     ├─ hybrid_search()      — 21 candidates (3 × top-k): RRF of cosine (0.6) and BM25 (0.4)
     ├─ dedup + MIN_SIMILARITY — drops identical texts and chunks with cosine < 0.45
     ├─ rerank()             — title boost, "servizio" category boost, same-source penalty
     └─ known facts first, then chunks, cut to RETRIEVAL_TOP_K (7)
     │
     ▼
6. build_context_block()  — one header per chunk: title, type, link (no link for MIGRATED_DOMAINS)
     │
     ▼
7. build_prompt()         — system prompt (IT/EN) + last 6 messages + context + today's date + question + suggested office
     │
     ▼
8. gap log                — 0 chunks, or best cosine < RETRIEVAL_CONFIDENCE (0.52)
     │
     ▼
9. LLM                    — Ollama, Claude API or Claude on AWS Bedrock, streaming or complete
     │
     ▼
Answer + sources (deduplicated; no URL for MIGRATED_DOMAINS)
```

When streaming finds no chunk at all, the LLM is not called: the answer is a fixed "not found" message followed by the suggested office.

### Follow-up Questions

With a conversation history, `contextualize_query()` asks the LLM (`CLAUDE_REWRITE_MODEL`, `BEDROCK_REWRITE_MODEL` or `OLLAMA_MODEL`) to rewrite the question as a standalone one, so that "and what are the requirements?" is searched together with its topic. The rewritten question drives retrieval, the office suggestion and the gap log; the LLM still receives the original question and the history. If the rewrite fails, is too long, or `QUERY_REWRITE=false`, the previous user question is appended instead.

### Known Facts

`config/known_facts.json` holds curated texts for topics the site covers poorly (opening hours, procedures, links). An entry is added to the context, ahead of the retrieved chunks and with score 1.0, when the question contains one of its `keywords` and one of its `require_any` words (whole single words: multi-word keywords never match). Retrieved chunks from the same source are dropped.

### Query Expansion

`expand_query()` appends the synonyms of every `config/synonyms.json` key found in the question (plain substring match). Examples:

- `"carta identità"` → `"documento identità CIE carta d'identità elettronica"`
- `"tari"` → `"tassa rifiuti raccolta rifiuti smaltimento rifiuti tributo rifiuti"`
- `"sue"` → `"sportello edilizia permesso costruire concessione"`

### Re-ranking

`rerank()` reorders chunks without using an additional model:

- **+0.02 for each query term** present in the chunk's title
- **+0.01** if the category is `"servizio"` (service pages)
- **-0.05 × n** if the same source has already appeared (penalizes duplicates)

Each chunk keeps its cosine score, which the confidence check and the sources list use.

### Office Suggestion

`suggest_office()` matches the question against the keywords in `config/offices.json` (whole words, case-insensitive, first match wins) and returns the office with a link (`SITE_URL` + `path`, or `url`). When an office matches, the suggestion goes into the prompt: with context, as an instruction to point to that office if the context does not really answer; without context, after the "nothing found" note.

### Response Cache

Complete answers from `/chat` (non-streaming) to questions without history are cached in memory, per API process, in an LRU of 200 entries with no expiry (emptied on every restart, e.g. after a sync). The key is the MD5 of the masked question, lowercased and stripped. `/chat/stream`, used by the widget, is never cached.

### Gap Log

Questions with 0 chunks, and *weak* retrievals where even the best chunk is below `RETRIEVAL_CONFIDENCE` (0.52), are appended to `data/gaps.jsonl` as `{ts, query, chunks, weak}`. `query` is the rewritten question after personal-data masking, truncated to 200 characters, with no user. Admin endpoint: `GET /gaps`. Entries expire after `RETENTION_GAPS_DAYS`.

---

## Crawler

`crawler/crawler.py` — async crawler based on httpx and BeautifulSoup. Requests are sequential, with a `CRAWL_DELAY_SECONDS` pause after each processed response (none after HTTP errors, skipped pages or, in incremental mode, unchanged ones), a 30-second timeout and redirects followed; cookies are cleared before every request (a WordPress view-counter cookie grew until Apache answered 400 to every request).

### How It Works

1. **BFS** (breadth-first) starting from `SITE_URL` and the `extra_start_urls` in `config/crawl_extra.json`
2. Follows only links to domains in `CRAWL_ALLOWED_DOMAINS`
3. Skips URLs containing any of the `CRAWL_EXCLUDE_PATTERNS` (plus `exclude_patterns` from `crawl_extra.json`), deeper than `CRAWL_MAX_PATH_DEPTH` (10 segments; per-domain limits in `domain_max_path_depth`), or repeating a non-numeric path segment (CMS breadcrumb loops)
4. For each HTML page (responses with HTTP status ≥ 400 are skipped):
   - Extracts text with `clean_text()` (removes nav, footer, widgets, noise lines)
   - Extracts the title with `extract_title()`
   - Extracts metadata with `extract_metadata()` (category, section, date, service status)
   - Saves a `.json` in `data/crawl_cache/pages/` (pages with less than 100 characters of text are skipped)
5. Downloads PDFs (links ending in `.pdf` on allowed domains) into `data/documents/`, keeping only HTTP 200 responses that start with `%PDF`; known PDFs whose file is missing or invalid are dropped from the base index so that they are fetched again (in incremental mode a PDF whose hash is unchanged is skipped and stays out of `index.json` until the next full sync)
6. Removes from state and index every known URL not reached in this run
7. Updates state in `data/crawl_cache/crawl_state.json`
8. Saves the index in `data/crawl_cache/index.json`

### Incremental Mode

```sh
python -m crawler.crawler --incremental
```

In incremental mode the crawler:
- Loads the existing index as a base
- Still downloads every page (to discover its links), but compares the MD5 hash of the decoded HTML text (raw bytes for PDFs) with the stored one and skips text extraction and saving when it is unchanged
- Marks the changed URLs, so that `scripts.sync incremental` re-indexes only those

State is managed by the `CrawlState` class in `crawler/state.py`.

### Metadata Extraction

`extract_metadata(html, url)` returns:

| Field            | How it is determined                                     |
|------------------|----------------------------------------------------------|
| `category`       | URL path: `/services/`, `/service/` → `servizio`; `/news/`, `/news-category/`, `/notizie/` → `notizia`; `/faq` → `faq`; `/documents/`, `/public_documents/` → `documento`; `/topics/` → `argomento`; otherwise `pagina` |
| `section`        | Host name: `amministrazionetrasparente` → `trasparenza`; `sportellounico`/`suap` → `suap`; `ambitosociale` → `ambito_sociale`; `servizi.` → `servizi_online`; otherwise `sito_principale` |
| `date`           | First of the meta tags `article:modified_time`, `article:published_time`, `date`, `DC.date`, `last-modified`, else the first `<time datetime>`; truncated to `YYYY-MM-DD` |
| `service_status` | `servizio` pages only: "servizio attivo" → `attivo`; "servizio non attivo"/"servizio sospeso" → `non attivo` |

### Text Cleaning

`clean_text()` takes the text from `<main>` (or the first element whose id contains content/main/body, then whose class contains content/main/article, then `<article>`, then `<body>`) and removes:
- Tags `<script>`, `<style>`, `<noscript>`, `<nav>`, `<header>`, `<footer>`, `<aside>`, `<form>`, `<iframe>`
- CSS elements with classes `feedback`, `rating`, `survey`, `cookie`, `breadcrumb`, `pagination`, etc.
- Noise lines: "go to page", "read more", "share", "print", page numbers, etc.
- Lines shorter than 4 characters

The result has one line per text block, without blank lines.

---

## Indexer

### Semantic Chunking (`indexer/chunker.py`)

The chunking strategy works in two levels:

1. **Split by paragraph** (blank lines). Crawled pages and PDFs come out of extraction with single newlines only, so in practice this applies to inbox TXT/DOCX files: a web page or a PDF is a single block
2. **If the block exceeds 1200 characters** (`MAX_PARAGRAPH_CHARS`): further split with `RecursiveCharacterTextSplitter` (150-character overlap; separators `. `, `, `, space)

For each chunk:
- The **page title** is prepended (improves semantic retrieval)
- Noise lines are removed (navigation, "access the service", "with SPID", etc.)
- Chunks shorter than **80 characters** are discarded

Each chunk's metadata includes `source` (URL), `title`, `doc_type` (`html`, `pdf`, or `document` for inbox TXT/DOCX), `chunk_index`, `chunk_total`; HTML chunks add `category`, `section`, `date`, `service_status`; crawled PDF chunks add `pdf_pages`; inbox chunks add `category`, `filename` and `origin: "inbox"`.

### Embedding (`indexer/embedder.py`)

Embeddings are generated via Ollama using the `bge-m3` model (1024 dimensions, multilingual, 8192-token context). Until October 2026 the model was `mxbai-embed-large`: on 100 questions about municipal services, `bge-m3` put the right page among the 7 chunks passed to the LLM 75 times against 62. To switch model see [Changing embedding model](#changing-embedding-model).

- Uses Ollama's `/api/embed` endpoint with **native batches** (16 texts per call)
- If a batch is rejected, its texts are sent one at a time; after 3 consecutive rejected batches, the rest of the run goes one text at a time
- 5xx and network errors: 3 retries with exponential backoff (1, 2, 4 s plus jitter)
- A text rejected for exceeding the model's context is cut client-side to 800, then 500, then 300 characters; other rejections fall back to the legacy `/api/embeddings` endpoint
- Control characters are replaced with spaces and texts are capped at 6000 characters; if everything fails the chunk gets a zero vector flagged `needs_reembedding` (`python -m scripts.sync reembed` computes it again)
- `embed_query()` for user queries (single call)

### Main Indexer (`indexer/indexer.py`)

```sh
python -m indexer.indexer              # index sources not yet in the vector store
python -m indexer.indexer --reset      # clear and re-index
python -m indexer.indexer --stats      # show statistics
python -m indexer.indexer --only-html  # HTML pages only
python -m indexer.indexer --only-pdf   # PDFs only
```

Reads `data/crawl_cache/index.json`. For each document it compares the new chunks with the stored ones: only new or changed chunks (or chunks with a zero vector) are embedded, in groups of 50; the others only have their metadata refreshed, and chunks of the same source that no longer exist are removed. Disk writes are deferred: a checkpoint every 30 minutes and one at the end.

### Document Inbox (`scripts/inbox_indexer.py`)

Allows indexing manually uploaded documents:

```sh
# Place files in:
data/inbox/resolution.pdf
data/inbox/resolution.json   # optional metadata

# Process manually:
python -m scripts.inbox_indexer

# Or in watch mode (polling every 60s; --interval to change it):
python -m scripts.inbox_indexer --watch
```

**Supported formats**: PDF, TXT, DOCX

**Optional metadata** (`.json` file alongside the document; defaults: file name without extension as title, `file://<name>` as source, category `documento`):
```json
{
    "title": "Resolution no. 15 of 2024",
    "source_url": "https://www.myorg.com/documents/2024/15",
    "category": "resolutions"
}
```

Processed files are moved to `data/inbox/processed/` (or `data/inbox/errors/` on failure, or with less than 100 characters of text). Inbox chunks are marked `origin: "inbox"`, so the stale-source cleanup of the syncs never removes them. Every `scripts.sync` mode except `reembed` also processes the inbox at the end.

---

## Vector Store

`indexer/vector_store.py` — numpy implementation, no external database.

### Data Structure

Three files on disk, written atomically (temporary file + rename):

| File                          | Content                          |
|-------------------------------|----------------------------------|
| `data/vectorstore/embeddings.npy` | numpy matrix (N × 1024) float32 |
| `data/vectorstore/metadata.json`  | List of dicts with chunk text and metadata |
| `data/vectorstore/ids.json`       | List of IDs (MD5 hashes)         |

### Cosine Search

Vectors are normalized at insertion. Search is a simple matrix product:

```python
scores = _embeddings @ query_vector   # cosine similarity
```

### Hybrid Search (BM25 + Vector)

`hybrid_search()` combines the two rankings via **Reciprocal Rank Fusion (RRF)**:

```
final_score(doc) = 0.6 × RRF_vector(doc) + 0.4 × RRF_bm25(doc)
RRF(rank) = 1 / (60 + rank + 1)
```

Each ranking considers top_k × 20 candidates. Results are ordered by fused score, but each hit's `score` is its cosine similarity, which `MIN_SIMILARITY` and `RETRIEVAL_CONFIDENCE` use.

The BM25 index (`BM25Okapi`, lowercase letter-only tokens: numbers are not indexed) is built when the store is loaded and rebuilt before the next search whenever chunks were added or removed. It requires the `rank-bm25` package; without it the search uses the vector ranking only.

### Idempotent Upsert

`upsert_chunks()` identifies each chunk with an MD5 hash of `source_url + chunk_index + text[:64]`. If the ID exists, vector and metadata are replaced in place; otherwise the chunk is appended. A chunk passed without a vector (unchanged text) only has its metadata refreshed; a zero vector is stored but flagged `needs_reembedding`. This makes the operation safe to run multiple times.

### Operational Note

The vector store is loaded into memory once per API process (at startup, or at the first query if Ollama was unreachable); `start.sh` runs two uvicorn processes (IPv4 and IPv6), each with its own copy. Chunks added by a standalone `indexer.indexer` or `inbox_indexer` run are not visible until the API restarts; `scripts.sync` restarts it at the end of every mode.

---

## Backend API

`api/main.py` (with `api/auth.py`) — FastAPI with SSE streaming, rate limiting, user login and admin authentication.

### Starting

```sh
# Development
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

# Production (FreeBSD): `service chatbot start` runs start.sh under daemon(8);
# start.sh launches two single-worker uvicorn processes on port 8000:
/opt/chatbot/venv/bin/python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 &   # IPv4
/opt/chatbot/venv/bin/python -m uvicorn api.main:app --host :: --port 8000 &        # IPv6
```

Each process keeps its own in-memory state (vector store, response cache, rate-limit counters). Login tokens are stateless, so either process validates them.

### Endpoints

#### `GET /health`

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

#### `POST /chat`

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
- `history`: optional, max 6 messages (3 turns); each `{role: "user"|"assistant", content: ≤2000 characters}`
- **Authentication**: with `AUTH_ENABLED=true`, `Authorization: Bearer <token>` (from `POST /auth/login`) is required; otherwise `401`
- Personal data recognizable in `question` and in the user messages of `history` is masked before reaching the model and the logs (`api/pii.py`); assistant messages are passed unchanged

**Response:**
```json
{
  "answer": "An identity card can be requested at the Registry Office...",
  "sources": [
    {"title": "Electronic Identity Card", "url": "https://...", "score": 0.87}
  ],
  "language": "en"
}
```

In `sources`, `url` is empty for documents on `MIGRATED_DOMAINS` (title only).

---

#### `POST /chat/stream`

Streaming response via **Server-Sent Events (SSE)**. This is the endpoint the widget uses.

**Rate limit, request and authentication**: same as `/chat`.

**Event stream** (each event is followed by a blank line):
```
data: {"token": "An "}
data: {"token": "identity "}
data: {"token": "card "}
...
data: {"sources": [{"title": "...", "url": "...", "score": 0.87}]}
data: {"done": true}
```

While waiting, the server sends `: keep-alive` comments every 15 seconds. If generation fails mid-stream:
```
data: {"error": "Errore durante la generazione"}
data: {"done": true}
```

Errors before the stream starts (400/401/422/429/500) are returned as normal JSON responses. When nothing relevant is found, the stream contains a single fallback token ("Non ho trovato informazioni specifiche…", plus the suggested office) and an empty `sources` list.

---

#### `POST /feedback`

Saves a 👍/👎 rating. With `AUTH_ENABLED=true` it requires `Authorization: Bearer <token>`.

**Rate limit**: 60 requests/hour per IP.

**Request:**
```json
{
  "question": "How do I apply for an identity card?",
  "answer": "An identity card can be requested...",
  "rating": -1,
  "comment": "The opening hours are out of date",
  "urls": ["https://www.example.org/registry-office"]
}
```

- `rating`: `1` (positive) or `-1` (negative)
- `comment` (optional, max 2000 characters) and `urls` (optional; http/https only, max 5 kept) can also be added later with `POST /feedback/detail`

**Response:** `{"ok": true, "id": "3f9c2a1b7d4e"}`

Saved in `data/feedback.jsonl`: the question (personal data masked, first 200 characters), the first 100 characters of the answer and, with login enabled, the user's id and name. A 👎 with a comment or links sends an email to `FEEDBACK_NOTIFY_EMAIL` (if SMTP is configured).

---

#### `GET /stats` *(admin)*

Vector store statistics.

```
Headers: X-Admin-Key: <ADMIN_API_KEY>
```

```json
{"collection": "numpy_store", "total_chunks": 5420, "unique_sources": 1830, "doc_types": {"html": 4100, "pdf": 1320}}
```

---

#### `GET /gaps?limit=50` *(admin)*

Latest questions with no retrieved content (`chunks: 0`) or only weakly relevant content (`weak: true`): content gaps to fill. Returns the last `limit` entries, oldest first.

```json
{
  "gaps": [
    {"ts": "2026-04-08T10:23:00+02:00", "query": "library hours", "chunks": 0, "weak": false}
  ],
  "total": 42
}
```

---

#### `GET /feedback/negative?limit=200` *(admin)*

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

#### `GET /feedback/list?limit=100` *(admin)*

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

#### Other endpoints

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

### Admin Authentication

Admin-only endpoints: `/stats`, `/gaps`, `/feedback/negative`, `/feedback/list`, `/feedback/resolve`, `/usage/summary`, `/usage/messages`, `/crawl-history`. They require the `X-Admin-Key` header with the value of `ADMIN_API_KEY` from `.env`; a missing or wrong key returns `403`. `/health` is public but returns its detailed view only with a valid key.

If `ADMIN_API_KEY` is empty, admin authentication is disabled (development only): all admin endpoints and the detailed `/health` are open to anyone.

### User Authentication

With `AUTH_ENABLED=true`, `/chat`, `/chat/stream`, `/feedback`, `/feedback/detail` and `/auth/me` require `Authorization: Bearer <token>`, obtained from `POST /auth/login`. Tokens are stateless, signed with HMAC-SHA256 using `AUTH_SECRET`, and valid for `AUTH_TOKEN_TTL_DAYS` days (default 30). Users are stored in `data/users.json` (password hashed with scrypt) and managed with `python -m scripts.adduser`. A missing, invalid or expired token returns `401`.

### CORS

The CORS middleware uses `API_CORS_ORIGINS` (comma-separated, no spaces; default `http://localhost:8000`). In production, set the exact site domain. Allowed methods: `GET`, `POST`, `OPTIONS`; allowed request headers: `Content-Type` and `Authorization`; credentials (cookies) are not allowed. `X-Admin-Key` is not an allowed header, so browsers can call the admin endpoints only from the API's own origin (e.g. the dashboard served at `/widget/dashboard.html`).

### Shutdown

`api/main.py` registers a SIGTERM handler that only logs the signal. It replaces uvicorn's own handler, so SIGTERM no longer stops uvicorn and the 2-second wait meant to follow it never runs: `service chatbot stop` therefore ends the service with SIGKILL (the daemon after 10 seconds, then the uvicorn processes).

### Static Files

The whole `widget/` directory is served by FastAPI under `/widget/` (`chatbot-widget.js`, `site-footer.js`, the pages, logos and icons). Every response has `Cache-Control: no-cache`, so browsers revalidate each time (304 if unchanged) and updates show up without a forced reload.

---

## Frontend Widget

`widget/chatbot-widget.js` — self-contained chat widget (injected JS + CSS, zero dependencies).

### Site Integration

Paste before `</body>` on all pages (or in the CMS template):

```html
<script>
  window.ChatbotConfig = {
    apiUrl:       'https://chatbot.myorg.com',
    primaryColor: '#003366',
    title:        'Zautte',
    subtitle:     'My Organization',
    position:     'right',
  };
</script>
<script src="https://chatbot.myorg.com/widget/chatbot-widget.js" defer></script>
```

The `widget/embed-snippet.html` file contains a ready-to-paste snippet with more options (`logoUrl`, `contactEmail`, `requireLogin`, `suggestions`, `infoUrl`, `privacyUrl`, `inline`).

### Configuration Options

| Option            | Default                              | Description |
|-------------------|--------------------------------------|-------------|
| `apiUrl`          | `'http://localhost:8000'`            | Backend API base URL (set it in production) |
| `primaryColor`    | `'#003366'`                          | Primary color (button, header, login) |
| `title`           | `'Assistente Virtuale'`              | Assistant name (header; login title "Accedi a …" / "Sign in to …") |
| `subtitle`        | `''`                                 | Subtitle in the panel header |
| `logoUrl`         | `''`                                 | Round header icon; a chat icon when empty |
| `position`        | `'right'`                            | `'right'` or `'left'` (floating button and panel) |
| `lang`            | from `navigator.language`            | Interface language: `'en'` = English, any other value = Italian |
| `welcomeIt` / `welcomeEn` | built-in text                | Welcome message; the AI notice is always shown below it |
| `suggestions`     | `[]`                                 | Suggested questions shown as chips under the welcome message |
| `contactEmail`    | `''`                                 | Adds a "report an error" email link to the AI notice and footer |
| `infoUrl`         | `''`                                 | "How it works" page linked in the AI notice and footer (e.g. `/widget/come-funziona.html`) |
| `privacyUrl`      | `''`                                 | Privacy notice linked in the AI notice and footer |
| `requireLogin`    | `false`                              | Show the email + password login before the chat (needs `AUTH_ENABLED=true`) |
| `loginSubtitle`   | `''`                                 | Optional text under the login title |
| `feedbackDetails` | `true`                               | After a 👎, show the comment/links form (`POST /feedback/detail`) |
| `inline`          | `null`                               | CSS selector or element: panel always open inside it, no floating or close button |
| `inlineHeight`    | `'min(640px, calc(100vh - 32px))'`   | Panel height in inline mode |
| `zIndex`          | `99999`                              | z-index of the button and panel |

### Features

- **SSE streaming** (`POST /chat/stream`): tokens arrive progressively and a cursor blinks during generation; if no token arrives within 10 seconds, a "Processing…" hint appears
- **Conversation history**: the last 3 turns (6 messages) are kept in memory and sent with each request; reset by **New conversation** (↻ in the header) and at login
- **Sources** under each answer, as links (title only when the URL is hidden for migrated domains)
- **Formatting**: `**bold**`, Markdown links, bare URLs and email addresses become clickable; everything else is HTML-escaped
- **Suggested questions** (`suggestions`): chips under the welcome message, removed at the first question
- **AI transparency**: "IA" badge next to the title; an AI notice always shown under the welcome message; footer "AI-generated answers · Do not enter personal data in the chat · Experimental service", plus "How it works" (`infoUrl`), "Privacy" (`privacyUrl`) and "Report an error" (`contactEmail`)
- **Feedback**: 👍/👎 under each answer → `POST /feedback`; after a 👎 (with `feedbackDetails`) a form for a comment and up to 5 links → `POST /feedback/detail`
- **Login** (`requireLogin`): email + password form (`/auth/login`), "Forgot password?" (`/auth/forgot`), sign-out button in the header; the token is kept in `localStorage` and sent as `Authorization: Bearer`, checked with `/auth/me` at startup; a `401` returns to the login form
- **Host-page API**: a `zautte:auth` event on `document` with `detail: {loggedIn, name}`, and `window.ZautteChatbot.logout()`
- **Inline mode** (`inline`): panel embedded in the page, always open, no floating or close button
- **Accessibility**: `role="dialog"`, messages in an `aria-live="polite"` region, `aria-hidden`/`aria-expanded`, focus management on open/close; `×` and Esc close the panel and return focus to the open button
- **Mobile** (≤ 480 px): full-screen panel, `font-size: 16px` on the input (prevents automatic zoom on iOS)

### Pages in `widget/`

- `dashboard.html`: colleagues see title, description and login, then the chat opens inside the page (`inline`). `dashboard.html#admin` asks for `ADMIN_API_KEY` and opens the admin view: service status, indexed content, activity, feedback, open 👎 with "Resolve", users, message log, token costs, crawl history and changelog
- `pilot.html`: pilot landing page with the floating widget
- `come-funziona.html`: public "How it works" page (AI transparency)
- `site-footer.js`: footer shared by the three pages, with the latest release and the last commit read from the public GitHub API (so each visitor's browser contacts `api.github.com`) and "Powered by" with `logo.png`

### Local Testing

The pages in `widget/` use `window.location.origin` as the API URL, so open them through the backend, not from disk: start it (`uvicorn api.main:app --port 8000 --reload`) and open `http://localhost:8000/widget/dashboard.html` (or `pilot.html`). Both pages set `requireLogin: true`: set `AUTH_ENABLED=true` and `AUTH_SECRET` in `.env` and create a user with `python -m scripts.adduser`.

---

## Periodic Operations

### Orchestrator `scripts/sync.py`

Single command to coordinate crawl + indexing:

```sh
# Full crawl + re-index of everything: the index is not cleared and stays usable;
# sources no longer on the site are removed (except MIGRATED_DOMAINS)
python -m scripts.sync full

# Incremental update: re-indexes changed pages only, removes stale HTML sources
python -m scripts.sync incremental

# Process inbox folder only
python -m scripts.sync inbox

# Clear the store and re-index from data/crawl_cache/ without crawling
# (see "Changing chunking configuration")
python -m scripts.sync full-index

# Re-embed only the chunks with a zero vector (failed embeddings)
python -m scripts.sync reembed
```

Every mode ends by killing the `uvicorn api.main:app` processes so that the API reloads the vector store; the watchdog starts the service again within a minute. The sync log is `/var/log/chatbot-sync.log`.

Only the cron wrappers (`incremental_sync.sh`, `full_sync.sh`) take the lock `/var/run/chatbot-sync.lock`. Two syncs writing the store at the same time can corrupt it, so run manual syncs under the same lock:

```sh
lockf -t 0 /var/run/chatbot-sync.lock venv/bin/python -m scripts.sync <mode>
```

During long runs the vector store is saved to disk every 30 minutes (checkpoint) and at the end.

### `/etc/crontab` (root) — production schedule

The sync wrappers need root (lock in `/var/run`, root-owned log, and every sync kills the root-owned uvicorn processes), so in production they run from `/etc/crontab`:

```
# minute hour mday month wday who  command
*        *    *    *     *    root /opt/chatbot/scripts/watchdog.sh
0        2    *    *     *    root /opt/chatbot/scripts/backup_vectorstore.sh
30       2    *    *     1    root /opt/chatbot/scripts/incremental_sync.sh >/dev/null 2>&1
0        3    1    *     *    root /opt/chatbot/scripts/full_sync.sh
```

| Schedule                       | Script                  | Description |
|--------------------------------|-------------------------|-------------|
| every minute                   | `watchdog.sh`           | Restarts the API if it is down or unresponsive |
| 02:00 every night              | `backup_vectorstore.sh` | Compressed vector store backup |
| 02:30 every Monday             | `incremental_sync.sh`   | Incremental sync; skipped if another sync holds the lock |
| 03:00 on the 1st of each month | `full_sync.sh`          | Full sync; waits up to 12 hours for the lock, output in `/var/log/chatbot/sync_full.log` |

`sync.py` also prints its log to stdout: without `>/dev/null`, cron tries to mail it and, with no local mailer, the job ends with exit 120.

### `scripts/cron_setup.sh`

The script (run it as root: `crontab -u` requires it) adds to the crontab of the user `chatbot` the incremental and full sync above, plus:

| Schedule                          | Command      | Description |
|-----------------------------------|--------------|-------------|
| every 30 min, 08:00–18:30 Mon–Fri | `sync inbox` | Processes inbox documents (no lock). As user `chatbot` it cannot kill the root-owned uvicorn processes, so the new documents are served only after the next API restart |
| 02:15 every day                   | `purge_logs` | Applies the log retention periods |
| 04:00 every Sunday                | `find … -size +10M` | Compresses logs larger than 10 MB in `/var/log/chatbot/` |

Since the sync wrappers need root (see above), prefer `/etc/crontab` for them.

Logs: the API writes to `/var/log/chatbot.log`; sync, watchdog and backup events go to `/var/log/chatbot-sync.log`; the cron jobs above write to `/var/log/chatbot/` (`sync_inbox.log`, `purge_logs.log`, `sync_full.log`).

### Vector Store Backup

`scripts/backup_vectorstore.sh` creates a compressed archive every night at 02:00:

```
data/backups/vectorstore_20260408_020000.tar.gz
```

Keeps the last **7 days** of backups, removing older ones automatically.

**Restore:**

```sh
cd /opt/chatbot
tar -xzf data/backups/vectorstore_YYYYMMDD_HHMMSS.tar.gz -C data/
service chatbot restart
```

### Watchdog

`scripts/watchdog.sh` runs every minute from `/etc/crontab`:

- if `/var/run/chatbot.maintenance` exists (created by `service chatbot stop`, removed by `service chatbot start`) it does nothing
- if the daemon in `/var/run/chatbot.pid` is not running, it runs `service chatbot start`
- if it is running but `GET http://127.0.0.1:8000/health` does not return `"status":"ok"`, it kills the daemon and the uvicorn processes (`kill -9`) and starts the service again

Events go to `/var/log/chatbot-sync.log`. The watchdog is also what brings the API back after every sync.

---

## System Service (FreeBSD)

### Installation

```sh
# Copy the rc.d script
cp /opt/chatbot/scripts/chatbot_rcd /usr/local/etc/rc.d/chatbot
chmod +x /usr/local/etc/rc.d/chatbot
chmod +x /opt/chatbot/start.sh      # executed by the rc.d script

# Enable the service
echo 'chatbot_enable="YES"' >> /etc/rc.conf

# Start
service chatbot start
```

The script expects the project in `/opt/chatbot` and starts after the `ollama` rc service.

### Management Commands

```sh
service chatbot start    # start
service chatbot stop     # stop
service chatbot restart  # restart
service chatbot status   # status
```

The `scripts/chatbot_rcd` script (runs as root):
- exports the variables in `/opt/chatbot/.env` (`python-dotenv` does not override them, so any `.env` change needs `service chatbot restart`; see the known limitation under [`.env` File](#env-file))
- starts `/opt/chatbot/start.sh` with `daemon(8)`: daemon PID in `/var/run/chatbot.pid`, stdout/stderr to `/var/log/chatbot.log`
- `start.sh` runs two uvicorn processes (one worker each) on port 8000, IPv4 `0.0.0.0` and IPv6 `::`; if one exits, the other is stopped
- `daemon` runs without `-r`: after a crash, or after a sync kills uvicorn, the watchdog restarts the service within a minute
- `stop` creates `/var/run/chatbot.maintenance` (the watchdog then leaves the service down) and `start` removes it; `status` checks the PID with `ps -p`

### Log Rotation

Copy the newsyslog configuration:

```sh
cp /opt/chatbot/scripts/newsyslog-chatbot.conf /etc/newsyslog.conf.d/chatbot.conf
```

Rotation configured (by size only):

| File                        | Rotations | Max size | Compression |
|-----------------------------|-----------|----------|-------------|
| `/var/log/chatbot.log`      | 14        | 50 MB    | bzip2 (J)   |
| `/var/log/chatbot-sync.log` | 14        | 100 MB   | bzip2 (J)   |

The file also lists `/var/log/chatbot-crawler.log` and `/var/log/chatbot-indexer.log`, which the current code no longer writes.

---

## Monitoring and Evaluation

### Health Check

```sh
curl http://127.0.0.1:8000/health                          # status, LLM provider, uptime
curl -H "X-Admin-Key: <key>" http://127.0.0.1:8000/health  # + chunks, last indexing, gaps, feedback, token costs
```

### Vector Store Statistics (admin)

```sh
curl -H "X-Admin-Key: <key>" http://127.0.0.1:8000/stats
```

### Content Gaps (admin)

```sh
curl -H "X-Admin-Key: <key>" http://127.0.0.1:8000/gaps
```

Shows the latest unanswered or weakly answered questions. Use them to identify topics to add to the site, documents to upload to the inbox, or known facts to write.

### Negative Feedback

```sh
venv/bin/python -m scripts.feedback_open             # open 👎 reports
venv/bin/python -m scripts.feedback_open --details   # only reports with a comment or links
```

The same reports are in the admin view of the dashboard, with a "Resolve" button (`POST /feedback/resolve`).

### Automated Evaluation Script

`scripts/eval.py` runs 9 test questions and measures retrieval and answer quality:

```sh
# Retrieval only (faster)
venv/bin/python -m scripts.eval --no-llm

# Retrieval + full LLM answer
venv/bin/python -m scripts.eval
```

**Metrics measured:**

| Metric               | Description                                              |
|----------------------|----------------------------------------------------------|
| Retrieval OK         | Questions for which sufficient chunks are found          |
| Keyword score        | % of expected keywords present in the answer (full mode only) |
| Average time         | Milliseconds per question (retrieval only with `--no-llm`, full answer otherwise) |

Results are saved in `data/eval_results.json`. In full mode each question goes through the normal answer path: with `claude` or `bedrock` it is a paid API call, and questions with no or weakly relevant retrieved content are logged in `gaps.jsonl`.

**Included test questions (replaceable with site-specific ones):** identity card, change of residence, school transport, access to records, library hours, waste tax, building permit, municipal police, nursery school.

---

## Privacy and Security

### GDPR

- **Conversation history** is kept client-side by the widget; the backend does not store conversations.
- **Personal data masking** (`api/pii.py`): tax codes, IBANs, payment card numbers, email addresses (except institutional domains in `PII_KEEP_EMAIL_DOMAINS`) and Italian phone numbers are replaced with a placeholder before the question (and the user's earlier messages in the conversation) reaches the LLM, the logs and the feedback archive. Feedback comments are stored as typed. Free-form data such as names cannot be detected reliably, so the widget still asks users not to enter personal data. Enabled by default (`PII_REDACTION=false` disables it).
- **What is stored**: `gaps.jsonl` (question text, no user), `feedback.jsonl` (rating, question, first 100 characters of the answer, comment and links, author for pilot users), `usage.jsonl` (question text, user id and metrics, pilot users only), `stats.json` (counters, the 100 most frequent questions, token history with user id; not covered by `purge_logs.py`), `resolved_negative.json` (resolution marks: feedback timestamp and question, the admin's note and the email address the notice was sent to), `users.json` (name, email and password hash of pilot users) and the application log `/var/log/chatbot.log` (beginning of each masked question; rotated by newsyslog).
- **Retention**: `scripts/purge_logs.py` deletes entries older than the `RETENTION_*_DAYS` settings (0 = keep forever), removes the question text from `usage.jsonl` earlier and drops the "resolved" marks of deleted feedback. `--dry-run` shows what it would delete. Run it daily via cron.
- **Privacy notice**: a draft is in `docs/informativa-privacy-zautte.md`; it must be approved by the DPO before publication. Link it from the widget with `privacyUrl`.
- **Where data goes**: with `LLM_PROVIDER=ollama` nothing leaves the server; with `claude` or `bedrock` the masked question, the last messages of the conversation and the retrieved passages are sent to the provider (see below). Embeddings are always computed locally by Ollama. The pages in `widget/` also load the latest release and commit from the public GitHub API in the visitor's browser.

### AI transparency

- The widget states at the first interaction that the user is talking to an AI system (EU AI Act, Art. 50), shows an "IA" badge in the header and an "AI-generated answers" note in the footer.
- `widget/come-funziona.html` explains in plain language how answers are produced, their limits and human oversight (Italian Law 132/2025, Art. 14). Link it from the widget with `infoUrl`. The page describes the San Benedetto del Tronto deployment with Claude: adapt the organization and the model provider before reusing it.

### Using Claude in a public administration

- **Direct Anthropic API** (`LLM_PROVIDER=claude`): EEA customers contract with Anthropic Ireland; the Data Processing Addendum (with EU Standard Contractual Clauses) is part of the Commercial Terms; API data is not used for training and is deleted within 30 days (up to 2 years for content flagged for usage-policy violations). Processing can take place outside the EU (no EU data-residency option as of October 2026), and the service is not in the Italian ACN catalogue of qualified cloud services.
- **Claude on AWS Bedrock** (`LLM_PROVIDER=bedrock`, requires `pip install "anthropic[bedrock]"`): with the `eu.` inference profile called from `eu-south-1` (Milan) requests are processed only in EU regions; AWS does not store prompts or completions and does not share them with Anthropic. AWS machine-learning services, Bedrock included, are ACN-qualified (QC2).
- In both cases the municipality is the data controller: it needs a DPIA, a record of processing activities and a privacy notice, and must purchase the service in line with public procurement rules.

### Rate Limiting

Implemented with slowapi (`api/limiter.py`), per client IP:

- `/chat` and `/chat/stream`: **20 requests/hour**
- `/feedback` and `/feedback/detail`: **60 requests/hour**
- `/auth/login`: **10/minute**; `/auth/forgot`: **5/hour**

Counters are kept in memory by each uvicorn process and reset on every restart (including the one at the end of each sync). Excess requests get `429`.

### Protected Admin Endpoints

`/stats`, `/gaps`, `/feedback/list`, `/feedback/negative`, `/feedback/resolve`, `/usage/summary`, `/usage/messages` and `/crawl-history` require the `X-Admin-Key` header, and `/health` returns its detailed fields only with it. Set `ADMIN_API_KEY` in `.env` in production: if it is empty, admin authentication is disabled. `/docs` and `/openapi.json` are public.

### CORS

Configured to accept requests only from origins in `API_CORS_ORIGINS`. In production, set the exact site domain.

### Reverse Proxy (recommended)

In production put nginx or caddy with TLS in front of the backend; the backend does not handle HTTPS. `start.sh` listens on all interfaces (`0.0.0.0` and `::`, port 8000), so restrict port 8000 with the firewall. `/chat/stream` already sends `X-Accel-Buffering: no`, which turns off nginx buffering for SSE: do not override it (or set `proxy_buffering off`). For the per-IP rate limits to see the real client, uvicorn must trust the proxy's `X-Forwarded-For`: set `FORWARDED_ALLOW_IPS` to the proxy address.

---

## Troubleshooting

### Backend not responding

```sh
service chatbot status
tail -f /var/log/chatbot.log
curl http://127.0.0.1:8000/health
ls /var/run/chatbot.maintenance   # if present, the watchdog will not restart the service
```

### Empty vector store (chunks=0 in all responses)

```sh
# Check how many chunks are indexed
curl -H "X-Admin-Key: <key>" http://127.0.0.1:8000/health   # → indexed_chunks
venv/bin/python -m indexer.indexer --stats                    # chunks on disk

# If 0: run a full sync
venv/bin/python -m scripts.sync full
```

### Ollama unreachable

```sh
ollama list                      # check available models
curl http://localhost:11434/api/tags
# If Ollama is not responding:
service ollama start             # or equivalent command on FreeBSD
```

### Changing embedding model

Vectors from different models (or from a different Ollama version, which can also change them) are not comparable: if `OLLAMA_EMBED_MODEL` changes, every chunk must be re-embedded. `scripts/reembed.py` does it without downtime and without crawling again. Example with `bge-m3`, the current default (it replaced `mxbai-embed-large` in October 2026):

```sh
ollama pull bge-m3
# 1. compute the new vectors in a cache, while production keeps using the old model
#    (resumable; identical texts are embedded once; --threads N limits the Ollama threads
#    to leave CPU to the API)
nice venv/bin/python -m scripts.reembed build --model bge-m3 --cache /data/chatbot/reembed/bge-m3
venv/bin/python -m scripts.reembed status --cache /data/chatbot/reembed/bge-m3
# 2. with no sync running: rewrite embeddings.npy (chunks added after the build are
#    embedded now), then switch the model in .env and restart the API
lockf -t 0 /var/run/chatbot-sync.lock venv/bin/python -m scripts.reembed apply \
    --model bge-m3 --cache /data/chatbot/reembed/bge-m3 --backup /data/chatbot/backups_keep
sed -i '' 's/^OLLAMA_EMBED_MODEL=.*/OLLAMA_EMBED_MODEL=bge-m3/' .env
service chatbot restart
```

The similarity thresholds and fusion weights in `api/rag.py` (`MIN_SIMILARITY`, `RETRIEVAL_CONFIDENCE`, `VECTOR_WEIGHT`/`BM25_WEIGHT`) depend on the model and must be calibrated again. A model with a different vector size also needs `EMBEDDING_DIMENSION` changed in `config/settings.py` before `apply`. On a new installation, simply clear the store and run `scripts.sync full`.

### Changing chunking configuration

Chunking is set by `MAX_PARAGRAPH_CHARS` in `indexer/chunker.py` and `CHUNK_OVERLAP` (minimum 150) in `config/settings.py`. To re-chunk the corpus already downloaded:

```sh
lockf -t 0 /var/run/chatbot-sync.lock venv/bin/python -m scripts.sync full-index
```

It does not crawl: it uses `data/crawl_cache/index.json`. It clears the store first, so chunks from `MIGRATED_DOMAINS` and inbox documents already in `data/inbox/processed/` are lost (move them back to `data/inbox/` to re-index them). Every chunk is re-embedded, which takes hours.

### Manual restart

```sh
# Restarts only the API; a running `python -m scripts.sync` is not affected
service chatbot restart
# Alternative: kill the uvicorn processes (IPv4 and IPv6) with SIGKILL (they ignore
# SIGTERM, see "Shutdown"); start.sh and daemon exit, and the watchdog restarts the
# service within a minute
pkill -9 -f "uvicorn api.main:app"
```

### Cron email with "Permission denied"

```sh
chmod +x /opt/chatbot/scripts/watchdog.sh
chmod +x /opt/chatbot/scripts/backup_vectorstore.sh
chmod +x /opt/chatbot/scripts/incremental_sync.sh
chmod +x /opt/chatbot/scripts/full_sync.sh
```

---

*Documentation updated: October 2026*
