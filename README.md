# Zautte — Technical Documentation

Zautte is a RAG (Retrieval-Augmented Generation) virtual assistant for any website. It answers user questions based exclusively on the indexed site's content, with multilingual support (Italian/English) and full privacy compliance (GDPR-ready).

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
                    ┌─────────────────────────────────────────┐
                    │           Site to be indexed             │
                    │        your-site.com  (HTML + PDF)       │
                    └──────────────┬──────────────────────────┘
                                   │ crawl
                                   ▼
                    ┌─────────────────────────────────────────┐
                    │             crawler/                     │
                    │  Downloads pages, extracts text/metadata │
                    │  Mode: full | incremental                │
                    └──────────────┬──────────────────────────┘
                                   │ index.json
                                   ▼
                    ┌─────────────────────────────────────────┐
                    │             indexer/                     │
                    │  Semantic chunking → Embedding (Ollama)  │
                    │  Upsert into vector store (numpy)        │
                    └──────────────┬──────────────────────────┘
                                   │ data/vectorstore/
                                   ▼
    User ─── JS Widget ─── api/main.py (FastAPI)
                                   │
                              api/rag.py
                         ┌─────────┴──────────┐
                    Hybrid Search           LLM (Ollama/Claude)
                  (cosine + BM25)          Streaming response
```

The system is **stateless**: conversations are not stored on the server. Turn history is managed client-side by the widget and sent with every request (max 3 turns = 6 messages).

---

## Technology Stack

| Component         | Technology                                           |
|-------------------|------------------------------------------------------|
| OS                | FreeBSD 14                                           |
| Python            | 3.11+                                                |
| Web scraping      | httpx + BeautifulSoup4/lxml                          |
| PDF parsing       | pypdf (pure Python, no compilation)                  |
| Embedding         | Ollama (`mxbai-embed-large`, 1024 dim)               |
| Vector store      | numpy (custom, pure Python)                          |
| Keyword search    | rank-bm25 (BM25Okapi)                                |
| LLM               | Local Ollama (`qwen2.5:7b`) or Claude API            |
| Backend           | FastAPI + uvicorn                                    |
| Rate limiting     | slowapi                                              |
| Frontend          | Vanilla JS/CSS (zero external dependencies)          |
| Supervisor        | FreeBSD rc.d + daemon(8)                             |
| Log rotation      | newsyslog                                            |

---

## Repository Structure

```
chatbot/
├── .env.example              # Environment variable template
├── requirements.txt          # Python dependencies
├── start.sh                  # Quick start (development)
│
├── config/
│   └── settings.py           # Centralized config (loads .env)
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
│   ├── main.py               # FastAPI: endpoints, rate limiting, admin auth
│   └── rag.py                # RAG pipeline: expand → retrieve → rerank → LLM
│
├── widget/
│   ├── chatbot-widget.js     # Chat widget (self-contained JS/CSS)
│   ├── embed-snippet.html    # Snippet to paste into the site
│   └── dashboard.html        # Control panel (restricted area)
│
├── scripts/
│   ├── sync.py               # Orchestrator: crawl + indexing
│   ├── inbox_indexer.py      # Indexing of manually uploaded documents
│   ├── eval.py               # RAG quality evaluation
│   ├── setup_freebsd.sh      # Initial setup on FreeBSD
│   ├── chatbot_rcd           # rc.d script for the service
│   ├── cron_setup.sh         # Configures cron jobs
│   ├── incremental_sync.sh   # Nightly incremental sync
│   ├── backup_vectorstore.sh # Daily vector store backup
│   ├── watchdog.sh           # Watchdog: restarts if unresponsive
│   └── newsyslog-chatbot.conf# Log rotation configuration
│
└── data/                     # Auto-generated (do not commit)
    ├── crawl_cache/          # Downloaded pages cache + index.json
    ├── documents/            # Downloaded PDFs
    ├── vectorstore/          # Embeddings + metadata (numpy)
    ├── inbox/                # Documents to index manually
    ├── backups/              # Compressed vector store backups
    ├── gaps.jsonl            # Unanswered queries (content gaps)
    └── feedback.jsonl        # User feedback (thumbs up/down)
```

---

## Installation

### Prerequisites

- FreeBSD 14 (or compatible)
- Python 3.11+
- [Ollama](https://ollama.com) installed and running (`ollama serve`)
- Ollama models downloaded:

```sh
ollama pull mxbai-embed-large   # embedding (1024 dim)
ollama pull qwen2.5:7b          # LLM (or qwen2.5:3b for lower RAM)
```

### Automated Setup

```sh
# Clone the repository
git clone <repo_url> /opt/chatbot
cd /opt/chatbot

# Run the setup script (as root)
sh scripts/setup_freebsd.sh
```

The script installs system packages (`python311`, `py311-pip`, etc.), creates the virtualenv, and installs Python dependencies.

### Manual Setup

```sh
cd /opt/chatbot

# Create and activate the virtualenv
python3.11 -m venv venv
. venv/bin/activate

# Install dependencies
pip install -r requirements.txt
pip install rank-bm25            # for BM25 hybrid search

# Configure the environment
cp .env.example .env
# Edit .env with your values

# Create data directories
mkdir -p data/vectorstore data/documents data/crawl_cache data/inbox
```

### First Indexing

```sh
# 1. Full site crawl (~6000 pages, takes hours)
venv/bin/python -m crawler.crawler

# 2. Generate embeddings and populate the vector store
venv/bin/python -m indexer.indexer

# Or in a single command (crawl + index + inbox):
venv/bin/python -m scripts.sync full
```

### Starting the Backend

```sh
# Development (with auto-reload)
venv/bin/python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

# Production (via rc.d — see dedicated section)
service chatbot start
```

---

## Configuration

All configuration lives in `config/settings.py`, which automatically loads variables from `.env` via `python-dotenv`.

### `.env` File

```ini
# LLM provider: "ollama" (local) or "claude" (Anthropic API)
LLM_PROVIDER=ollama

# Ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:7b
OLLAMA_EMBED_MODEL=mxbai-embed-large

# Claude API (requires DPA with Anthropic for public sector use)
ANTHROPIC_API_KEY=

# Backend
API_HOST=127.0.0.1
API_PORT=8000
API_CORS_ORIGINS=https://www.your-site.com

# Admin key for protected endpoints (/stats, /gaps, /feedback/list)
# Leave empty to disable authentication (development only)
ADMIN_API_KEY=your-secret-key-here
```

### Key Parameters in `settings.py`

| Parameter              | Default                        | Description                                          |
|------------------------|--------------------------------|------------------------------------------------------|
| `SITE_URL`             | *(from .env)*                  | Root URL for crawling                                |
| `CRAWL_MAX_PAGES`      | `10000`                        | Maximum pages to crawl                               |
| `CRAWL_DELAY_SECONDS`  | `1.0`                          | Delay between requests (server courtesy)             |
| `CRAWL_ALLOWED_DOMAINS`| *(from .env)*                  | Domains allowed during crawl                         |
| `CRAWL_EXCLUDE_PATTERNS`| Lists of patterns to exclude  | URLs to ignore (admin, feeds, images, etc.)          |
| `CHUNK_SIZE`           | `800`                          | Target chunk size (characters)                       |
| `CHUNK_OVERLAP`        | `100`                          | Overlap between chunks (minimum — effective is 150)  |
| `OLLAMA_EMBED_MODEL`   | `mxbai-embed-large`            | Embedding model (1024 dim)                           |
| `EMBEDDING_DIMENSION`  | `1024`                         | Embedding vector dimension                           |
| `RETRIEVAL_TOP_K`      | `5`                            | Chunks to retrieve per query                         |
| `LLM_PROVIDER`         | `ollama`                       | `ollama` or `claude`                                 |
| `OLLAMA_MODEL`         | `llama3.1:8b`                  | Local LLM model                                      |
| `CLAUDE_MODEL`         | `claude-sonnet-4-6`            | Claude API model                                     |

---

## RAG Pipeline

Each question flows through this pipeline in `api/rag.py`:

```
User question
     │
     ▼
1. expand_query()        — adds domain synonyms (e.g. "TARI" → "waste tax")
     │
     ▼
2. embed_query()         — vectorizes the expanded query (Ollama mxbai-embed-large)
     │
     ▼
3. hybrid_search()       — cosine similarity (60%) + BM25 (40%) with RRF
     │
     ▼
4. MIN_SIMILARITY filter — discards chunks with score < 0.45
     │
     ▼
5. rerank()              — title boost, "service" category boost, duplicate penalty
     │
     ▼
6. build_context_block() — formats chunks with metadata (category, status, date)
     │
     ▼
7. detect_language()     — detects Italian vs English (keyword heuristic)
     │
     ▼
8. build_prompt()        — system prompt + conversation history + context + question
     │
     ▼
9. LLM (Ollama/Claude)   — generates response (streaming or complete)
     │
     ▼
Answer + sources
```

### Query Expansion

`expand_query()` looks for domain-specific keywords in the query and enriches them with predefined synonyms (configurable in `config/synonyms.json`). Examples:

- `"identity card"` → adds `"ID document CIE electronic identity card"`
- `"TARI"` → adds `"waste tax refuse collection"`
- `"SUE"` → adds `"building permit construction license concession"`

### Re-ranking

`rerank()` adjusts chunk scores without using an additional model:

- **+0.02 for each query term** present in the chunk's title
- **+0.01** if the category is `"service"` (more useful to the user)
- **-0.05 × n** if the same source has already appeared (penalizes duplicates)

### Office Suggestion

When no relevant chunk is found (`chunks == 0`), `suggest_office()` analyzes the query and suggests the relevant contact with a direct link. Offices/contacts are configurable in `config/offices.json`.

### Response Cache

Responses to identical queries (without conversation history) are cached in memory with an LRU cache of 200 entries. The cache key is the MD5 hash of the normalized query (lowercase, stripped).

### Gap Log

Every query that produces 0 chunks is recorded in `data/gaps.jsonl` (timestamp + text truncated to 200 chars, no personal data). Accessible via the admin endpoint `GET /gaps`.

---

## Crawler

`crawler/crawler.py` — async crawler based on httpx and BeautifulSoup.

### How It Works

1. **BFS** (breadth-first) starting from `SITE_URL`
2. Follows only links to domains in `CRAWL_ALLOWED_DOMAINS`
3. Skips URLs matching `CRAWL_EXCLUDE_PATTERNS`
4. For each HTML page:
   - Extracts text with `clean_text()` (removes nav, footer, widgets, noise lines)
   - Extracts the title with `extract_title()`
   - Extracts metadata with `extract_metadata()` (category, section, date, service status)
   - Saves a `.json` in `data/crawl_cache/pages/`
5. Downloads PDFs found in pages
6. Updates state in `data/crawl_cache/crawl_state.json`
7. Saves the index in `data/crawl_cache/index.json`

### Incremental Mode

```sh
python -m crawler.crawler --incremental
```

In incremental mode the crawler:
- Loads the existing index as a base
- Compares the MD5 hash of each page's content with the stored one
- Skips unchanged pages (much faster)
- Removes from the index pages that have disappeared from the site

State is managed by the `CrawlState` class in `crawler/state.py`.

### Metadata Extraction

`extract_metadata(html, url)` returns:

| Field            | How it is determined                                     |
|------------------|----------------------------------------------------------|
| `category`       | URL pattern (`/services/` → `service`, etc.)            |
| `section`        | Subdomain (`transparency` → `transparency`)             |
| `date`           | Meta tags `article:modified_time`, `date`, or `<time>`  |
| `service_status` | Text "service active/inactive" in the page              |

### Text Cleaning

`clean_text()` removes:
- Tags `<script>`, `<style>`, `<nav>`, `<header>`, `<footer>`, `<aside>`, `<form>`, `<iframe>`
- CSS elements with classes `feedback`, `rating`, `survey`, `cookie`, `breadcrumb`, `pagination`, etc.
- Noise lines: "go to page", "read more", "share", "print", page numbers, etc.
- Lines shorter than 4 characters

---

## Indexer

### Semantic Chunking (`indexer/chunker.py`)

The chunking strategy works in two levels:

1. **Split by paragraph** (`\n\n`): preserves whole units of meaning
2. **If the paragraph exceeds 1200 characters**: further split with `RecursiveCharacterTextSplitter` and 150-character overlap

For each chunk:
- The **page title** is prepended (improves semantic retrieval)
- Noise lines are removed (navigation, "access the service", "with SPID", etc.)
- Chunks shorter than **80 characters** are discarded

Each chunk's metadata includes: `source` (URL), `title`, `doc_type` (`html`/`pdf`), `chunk_index`, `chunk_total`, plus additional page metadata (`category`, `section`, `date`, `service_status`).

### Embedding (`indexer/embedder.py`)

Embeddings are generated via Ollama using the `mxbai-embed-large` model (1024 dimensions, good multilingual support).

- Uses Ollama's `/api/embed` endpoint with **native batches** (32 texts per call)
- Automatic fallback to `/api/embeddings` endpoint (one at a time) if batch fails
- `embed_query()` for user queries (single call)

### Main Indexer (`indexer/indexer.py`)

```sh
python -m indexer.indexer              # index everything
python -m indexer.indexer --reset      # clear and re-index
python -m indexer.indexer --stats      # show statistics
python -m indexer.indexer --only-html  # HTML pages only
python -m indexer.indexer --only-pdf   # PDFs only
```

Reads `data/crawl_cache/index.json` and processes in batches of 50 chunks at a time (embedding + upsert into vector store).

### Document Inbox (`scripts/inbox_indexer.py`)

Allows indexing manually uploaded documents:

```sh
# Place files in:
data/inbox/resolution.pdf
data/inbox/resolution.json   # optional metadata

# Process manually:
python -m scripts.inbox_indexer

# Or in watch mode (polling every 60s):
python -m scripts.inbox_indexer --watch
```

**Supported formats**: PDF, TXT, DOCX

**Optional metadata** (`.json` file alongside the document):
```json
{
    "title": "Resolution no. 15 of 2024",
    "source_url": "https://www.myorg.com/documents/2024/15",
    "category": "resolutions"
}
```

Processed files are moved to `data/inbox/processed/` (or `data/inbox/errors/` on failure).

---

## Vector Store

`indexer/vector_store.py` — numpy implementation, zero dependencies to compile.

### Data Structure

Three files on disk:

| File                          | Content                          |
|-------------------------------|----------------------------------|
| `data/vectorstore/embeddings.npy` | numpy matrix (N × 1024) float32 |
| `data/vectorstore/metadata.json`  | List of dicts with chunk metadata |
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

The BM25 index is built in memory on first access after each save. Requires the `rank-bm25` package.

### Idempotent Upsert

`upsert_chunks()` identifies each chunk with an MD5 hash of `source_url + chunk_index + text[:64]`. If the chunk already exists, it updates the embedding in place; otherwise it appends it. This makes the operation safe to run multiple times.

### Operational Note

The vector store is loaded **once** into memory when the API process starts. New chunks added by the indexer while the API is running are not visible until the service is restarted.

---

## Backend API

`api/main.py` — FastAPI with SSE streaming, rate limiting, and admin authentication.

### Starting

```sh
# Development
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

# Production (2 workers)
uvicorn api.main:app --host 127.0.0.1 --port 8000 --workers 2
```

### Endpoints

#### `GET /health`

Service status. No authentication required.

```json
{
  "status": "ok",
  "indexed_chunks": 5420,
  "llm_provider": "ollama",
  "llm_model": "qwen2.5:7b"
}
```

---

#### `POST /chat`

Complete response (non-streaming). Waits for the full response before replying.

**Rate limit**: 20 requests/hour per IP; 200 requests/hour globally.

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
- `history`: optional, max 6 messages (3 turns)

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

---

#### `POST /chat/stream`

Streaming response via **Server-Sent Events (SSE)**.

**Rate limit**: same as `/chat`.

**Request**: same as `/chat`.

**Event stream:**
```
data: {"token": "An "}
data: {"token": "identity "}
data: {"token": "card "}
...
data: {"sources": [{"title": "...", "url": "...", "score": 0.87}]}
data: {"done": true}
```

On error during stream:
```
data: {"error": "Error during generation"}
```

---

#### `POST /feedback`

Saves user feedback (thumbs up/down). No personal data stored.

**Rate limit**: 60 requests/hour per IP.

**Request:**
```json
{
  "question": "How do I apply for an identity card?",
  "answer": "An identity card can be requested...",
  "rating": 1
}
```

- `rating`: `-1` (negative) or `1` (positive)

---

#### `GET /stats` *(admin)*

Vector store statistics.

```
Headers: X-Admin-Key: <ADMIN_API_KEY>
```

```json
{"collection": "numpy_store", "total_chunks": 5420}
```

---

#### `GET /gaps?limit=50` *(admin)*

Latest unanswered queries (content gaps to fill).

```json
{
  "gaps": [
    {"ts": "2026-04-08T10:23:00", "query": "library hours", "chunks": 0}
  ],
  "total": 42
}
```

---

#### `GET /feedback/negative?limit=200`

Questions that received negative feedback (👎). Returns only `question` and `ts`; no personal data. No authentication required. Used by the dashboard to automatically display unsatisfied queries.

```json
{
  "items": [
    {"question": "How do I book an appointment?", "ts": "2026-04-22T18:03:50"}
  ],
  "total_negative": 8,
  "total": 30
}
```

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

### Admin Authentication

The `/stats`, `/gaps`, and `/feedback/list` endpoints require the `X-Admin-Key` header with the value of `ADMIN_API_KEY` from `.env`. The `/feedback/negative` endpoint is public (returns only question text, no personal data).

If `ADMIN_API_KEY` is empty, authentication is disabled (development only).

### CORS

The CORS middleware is configured with `API_CORS_ORIGINS` (default: `http://localhost:8000`). In production, set the exact site domain. Allowed methods: `GET`, `POST`, `OPTIONS`.

### Graceful Shutdown

The backend intercepts `SIGTERM` and waits 2 seconds before terminating, to allow in-flight streaming responses to complete.

### Static Widget

The `widget/chatbot-widget.js` file is served as a static file by FastAPI at the path `/widget/chatbot-widget.js`.

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

The `widget/embed-snippet.html` file contains the ready-to-paste snippet.

### Configuration Options

| Option         | Default          | Description                              |
|----------------|------------------|------------------------------------------|
| `apiUrl`       | *(required)*     | Backend API base URL                     |
| `primaryColor` | `#003366`        | Widget primary color                     |
| `title`        | `'Virtual Assistant'` | Assistant name                      |
| `subtitle`     | `''`             | Subtitle in the panel header             |
| `position`     | `'right'`        | `'right'` or `'left'`                   |
| `lang`         | `navigator.language` | Forced language (`'it'`, `'en'`, etc.) |

### Features

- **SSE Streaming**: tokens arrive progressively, cursor blinks during generation
- **Conversation history**: keeps the last 3 turns (6 messages) and sends them with each request
- **Suggested questions**: clickable chips at panel open to guide the user
- **Feedback**: thumbs up/down buttons after each answer, sent to `POST /feedback`
- **Wait hint**: after 10 seconds without a response, "Processing…" text appears
- **Accessibility**: `aria-hidden`, `aria-expanded`, focus management on open/close
- **Mobile**: `font-size: 16px` on input (prevents automatic zoom on iOS)
- **Close button**: `×` in the top right, returns focus to the open button

### Local Testing

Open `widget/dashboard.html` in the browser. Requires the backend to be reachable at the address configured in `apiUrl`.

---

## Periodic Operations

### Orchestrator `scripts/sync.py`

Single command to coordinate crawl + indexing:

```sh
# First installation (full crawl + index from scratch)
python -m scripts.sync full

# Nightly update (changes only)
python -m scripts.sync incremental

# Process inbox folder only
python -m scripts.sync inbox

# Re-index without new crawl (after changing chunking config)
python -m scripts.sync full-index
```

### Cron Jobs

Configure with:

```sh
sh scripts/cron_setup.sh
```

Installed schedule (user `chatbot`):

| Schedule                         | Command                         | Description                     |
|----------------------------------|---------------------------------|---------------------------------|
| 02:30 every night                | `sync incremental`              | Updates changes only            |
| 03:00 every Sunday               | `sync full`                     | Full weekly scan                |
| every 30 min (8–18, Mon–Fri)     | `sync inbox`                    | Processes inbox documents       |

Logs are written to `/var/log/chatbot/`.

### `/etc/crontab` (root) — watchdog and backup

```
* * * * * root /opt/chatbot/scripts/watchdog.sh
0 2 * * * root /opt/chatbot/scripts/backup_vectorstore.sh
```

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

`scripts/watchdog.sh` runs every minute via cron. If the backend does not respond to `GET /health`, it restarts it via `service chatbot start`.

---

## System Service (FreeBSD)

### Installation

```sh
# Copy the rc.d script
cp /opt/chatbot/scripts/chatbot_rcd /usr/local/etc/rc.d/chatbot
chmod +x /usr/local/etc/rc.d/chatbot

# Enable the service
echo 'chatbot_enable="YES"' >> /etc/rc.conf

# Start
service chatbot start
```

### Management Commands

```sh
service chatbot start    # start
service chatbot stop     # stop
service chatbot restart  # restart
service chatbot status   # status
```

The `scripts/chatbot_rcd` script uses `daemon(8)` to:
- Write the PID to `/var/run/chatbot.pid`
- Redirect stdout/stderr to `/var/log/chatbot.log`
- Automatically restart on crash (`-r`)
- Load environment variables from `.env` before starting
- Launch uvicorn with 2 workers

### Log Rotation

Copy the newsyslog configuration:

```sh
cp /opt/chatbot/scripts/newsyslog-chatbot.conf /etc/newsyslog.conf.d/chatbot.conf
```

Rotation configured:

| File                      | Rotations | Max size | Compression  |
|---------------------------|-----------|----------|--------------|
| `/var/log/chatbot.log`    | 7         | 10 MB    | gzip (J)     |
| `/var/log/chatbot-sync.log` | 7       | 5 MB     | gzip (J)     |

---

## Monitoring and Evaluation

### Health Check

```sh
curl http://127.0.0.1:8000/health
```

### Vector Store Statistics (admin)

```sh
curl -H "X-Admin-Key: <key>" http://127.0.0.1:8000/stats
```

### Content Gaps (admin)

```sh
curl -H "X-Admin-Key: <key>" http://127.0.0.1:8000/gaps
```

Shows the latest unanswered questions. Use them to identify topics to add to the site or documents to upload to the inbox.

### Automated Evaluation Script

`scripts/eval.py` runs 10 test questions and measures retrieval and answer quality:

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
| Keyword score        | % of expected keywords present in the answer            |
| Average time         | Milliseconds per response (end-to-end)                   |

Results are saved in `data/eval_results.json`.

**Included test questions (replaceable with site-specific ones):** identity card, change of residence, school transport, access to records, library hours, waste tax, building permit, municipal police, nursery school.

---

## Privacy and Security

### GDPR

- **No conversations stored**: the backend is stateless. History is managed entirely client-side by the widget.
- **Gap log**: records only the query text (truncated to 200 characters) and timestamp, without session data or IP.
- **Feedback**: records rating, question and answer preview (truncated), without user identifiers.
- **Local Ollama**: no data sent to external servers; everything stays on the local server.
- **Claude API**: requires a DPA (Data Processing Agreement) with Anthropic. For Italian public sector organizations, verify applicable regulatory requirements.

### Rate Limiting

- `/chat` and `/chat/stream`: **20 requests/hour per IP**, **200/hour globally**
- `/feedback`: **60 requests/hour per IP**

### Protected Admin Endpoints

`/stats`, `/gaps`, `/feedback/list` require the `X-Admin-Key` header. Set `ADMIN_API_KEY` in `.env` in production. `/feedback/negative` is public.

### CORS

Configured to accept requests only from origins in `API_CORS_ORIGINS`. In production, set the exact site domain.

### Reverse Proxy (recommended)

In production, run the backend listening on `127.0.0.1:8000` and use nginx or caddy as a reverse proxy with TLS. The backend does not handle HTTPS directly. Set the `X-Accel-Buffering: no` header in nginx for SSE streaming.

---

## Troubleshooting

### Backend not responding

```sh
service chatbot status
tail -f /var/log/chatbot.log
curl http://127.0.0.1:8000/health
```

### Empty vector store (chunks=0 in all responses)

```sh
# Check how many chunks are indexed
curl http://127.0.0.1:8000/health   # → indexed_chunks

# If 0: run indexing
venv/bin/python -m indexer.indexer --stats
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

If `OLLAMA_EMBED_MODEL` is changed, the vector store must be cleared and re-indexed (incompatible embedding dimensions):

```sh
rm -rf data/vectorstore/
venv/bin/python -m scripts.sync full
```

### Changing chunking configuration

If chunking parameters are modified and you want to apply them to the entire already-downloaded corpus:

```sh
venv/bin/python -m scripts.sync full-index
```

Does not perform a new crawl: uses files already in `data/crawl_cache/`.

### Manual restart without stopping the indexer

```sh
# Find the PID of the uvicorn process (not the daemon wrapper)
pgrep -f "uvicorn api.main"
kill <PID>
# The daemon restarts it automatically if chatbot_enable="YES"
```

### Cron email with "Permission denied"

```sh
chmod +x /opt/chatbot/scripts/watchdog.sh
chmod +x /opt/chatbot/scripts/backup_vectorstore.sh
chmod +x /opt/chatbot/scripts/incremental_sync.sh
```

---

*Documentation updated: April 2026*
