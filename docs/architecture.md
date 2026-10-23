<sub>[Zautte](../README.md) › [Documentation](README.md) › Architecture</sub>

# Architecture

How the pieces fit together, the technology stack and the repository layout.

## Overview

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

The API stores no conversations on disk: the last 3 turns of each conversation (at most 5000 conversations, two hours, api/conversations.py) live only in the API's memory, and the widget holds just the conversation id. Single questions are still logged, after personal-data masking, in `data/usage.jsonl` (logged-in users only), `data/gaps.jsonl` and `data/feedback.jsonl`, and the answers given in `data/answers.jsonl` (logged-in users only), subject to the `RETENTION_*` periods and with an id (`rid`) that lets `scripts/forget.py` delete them on request; `data/stats.json` also keeps the 100 most frequent questions, outside those periods. The API log records only the question id (see [Privacy and Security](privacy-and-security.md)).

## Processes, accounts and ports

- **API**: one uvicorn process (`api/serve.py`, started by `start.sh` under the rc.d service). It runs as an unprivileged service account (`chatbot_user` in rc.conf); `scripts/chatbot_rcd` refuses to run it as root unless `chatbot_allow_root="YES"`. The supervisor `daemon(8)` stays root only to own the pid file and the log. It listens on `API_HOST` (default `127.0.0.1,::1`), port `API_PORT` (default 8000).
- **Sync jobs** (`full_sync.sh`, `incremental_sync.sh`, `news_sync.sh`, `sync inbox`): run as the unprivileged account `zsync` (`scripts/setup_sync_user.sh`), which reads `.env.sync` (an allowlist built by `make_sync_env.py`, no secrets). They write only `vectorstore`, `crawl_cache`, `documents`, `inbox` and `backups`.
- **Watchdog** (`scripts/watchdog.sh`, root, cron every minute): restarts the API if `/health` does not answer, and carries out the `restart`/`reload` request files that sync jobs leave in `SYNC_CONTROL_DIR`.
- **Ollama** (default `http://localhost:11434`, local only): embeddings and the optional local LLM; it should run as its own unprivileged account too.

---

## Technology Stack

| Component         | Technology                                           |
|-------------------|------------------------------------------------------|
| OS                | FreeBSD 15 (15.1-RELEASE tested)                |
| Python            | 3.11+                                                |
| Web scraping      | httpx + BeautifulSoup4/lxml                          |
| PDF parsing       | pypdf (pure Python, no compilation)                  |
| Other documents   | python-docx (DOCX files in the inbox)                |
| Chunking          | langchain-text-splitters                             |
| Embedding         | Ollama (`bge-m3`, 1024 dim, multilingual)            |
| Vector store      | Custom in-memory store on numpy (no external database) |
| Keyword search    | rank-bm25 (BM25Okapi)                                |
| LLM               | Local Ollama (`llama3.1:8b` by default) or Claude via the Anthropic API or AWS Bedrock (`anthropic` SDK; default: Claude Sonnet 4.6) |
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
├── start.sh                  # Launcher run by the rc.d service (uvicorn on the addresses of API_HOST, port API_PORT)
├── requirements-lock.txt     # Exact versions used in production, with the SHA-256 of each file (scripts/update_lock_hashes.py)
├── README.md, CHANGELOG.md, LICENSE, CONTRIBUTING.md, SECURITY.md, CODE_OF_CONDUCT.md, ACCESSIBILITY.md
├── version.json, CITATION.cff, publiccode.yml  # Release metadata, kept equal by scripts/check_version.py
├── .github/                  # CI (ruff, version check, accessibility check), CodeQL, release workflow, Dependabot, issue/PR templates
├── tests/unit/               # Regression tests (pytest), run in CI
├── tests/a11y/check.py       # Accessibility check run in CI
│
├── config/
│   ├── settings.py           # Centralized config (loads .env)
│   ├── known_facts.json      # Curated facts injected for specific topics (site-specific, not in the repo: copy known_facts.example.json)
│   ├── synonyms.json         # Query expansion synonyms (site-specific, see synonyms.example.json)
│   ├── offices.json          # Office suggested when the question matches its keywords (site-specific, see offices.example.json)
│   └── crawl_extra.json      # Site-specific crawl overrides (see crawl_extra.example.json): extra seed URLs, exclude patterns, per-domain path depth, URL aliases
│
├── crawler/
│   ├── crawler.py            # Async httpx crawler
│   ├── netpolicy.py          # One policy for every download: allowed hosts, public addresses only, pinned connection, bounded size
│   └── state.py              # Crawl state for incremental mode
│
├── indexer/
│   ├── chunker.py            # Semantic paragraph chunking
│   ├── embedder.py           # Embedding generation via Ollama
│   ├── indexer.py            # Orchestrator: crawler output → vector store
│   ├── pdf_extractor.py      # Text extraction from PDF (pypdf)
│   ├── titles.py             # Readable titles for PDF sources (chosen at query time)
│   └── vector_store.py       # numpy store: cosine search + BM25 hybrid
│
├── api/
│   ├── main.py               # FastAPI: endpoints, rate limiting, admin auth, static /widget
│   ├── rag.py                # RAG pipeline: rewrite → retrieve → rerank → LLM
│   ├── auth.py               # User login (pilot), stateless HMAC tokens
│   ├── pii.py                # Masks personal data in questions before LLM and logs
│   ├── smalltalk.py          # Greetings, thanks, insults: fixed reply, no retrieval or LLM
│   ├── datalog.py            # Rewrites the user logs: retention and deletion on request
│   ├── feedback_store.py     # Feedback archive (votes, comments, links)
│   ├── mailer.py             # Email notifications (credentials, reports)
│   ├── limiter.py            # Rate limiting (slowapi)
│   ├── account_guard.py      # Limits per account: wrong passwords, reset emails
│   ├── bodylimit.py          # Limit on the size of a request body
│   ├── csp.py                # Content-Security-Policy of the pages (hashes of the inline scripts)
│   ├── clientkey.py          # Key of a client for the limits (IPv6 by /64)
│   ├── llm_gate.py           # Limits on answers generated at once (per user and in total)
│   ├── maintenance.py        # "Service under maintenance" while the model provider refuses to serve (credit, key)
│   ├── filelock.py           # Safe file locks (no symlinks, fail closed)
│   ├── safefile.py           # Atomic writes and regular-file checks for the data files
│   ├── privdrop.py           # Maintenance scripts started as root switch to the service account
│   ├── serve.py              # Starts the single uvicorn process on the addresses of API_HOST
│   ├── conversations.py      # Last turns of each conversation, in memory
│   ├── admin_guard.py        # Admin key check with a lockout after repeated wrong keys
│   └── quota.py              # Daily question limit per user
│
├── widget/                   # served by the API under /widget/
│   ├── chatbot-widget.js     # Chat widget (self-contained JS/CSS)
│   ├── embed-snippet.html    # Snippet to paste into the site
│   ├── dashboard.html        # Generic page: login + chat inside the page
│   ├── dashboard.css         # Styles of the page
│   ├── come-funziona.html    # Generic "how it works" page (AI transparency)
│   ├── pilot.html            # Test page with the floating widget
│   ├── site-footer.js        # Footer shared by the pages (latest release and commit, Zenodo DOI, accessibility link, client IP)
│   ├── matomo.js             # Optional page-view statistics on your Matomo (no cookies, never the questions)
│   ├── zautte-logo.svg       # Zautte logo (also used at the top of the README)
│   ├── zautte-icon.svg       # "ZA" icon in the chat header
│   ├── zautte-favicon.svg    # Favicon of the pages
│   ├── zautte-icon-180.png   # apple-touch-icon
│   └── manifest.webmanifest  # Web app manifest
│
├── admin_panel/              # restricted area: login.html is the page at ADMIN_PATH; panel.html/css/js are
│                             # delivered by ADMIN_PATH/dati only with a valid key (changelog.html: optional, per installation)
│
├── docs/                     # This documentation (index: docs/README.md)
│   ├── architecture.md, installation.md, configuration.md, how-it-works.md,
│   │   api.md, widget.md, operations.md, privacy-and-security.md, releasing.md,
│   │   accessibility-testing.md
│   ├── images/               # Screenshots used in the README
│   └── zautte-logo-dark.svg  # White logo for GitHub's dark theme
│
├── scripts/
│   ├── sync.py               # Orchestrator: crawl + indexing (full, incremental, inbox, full-index, reembed, news)
│   ├── news_sync.py          # News in near real time: configurable sources (WordPress API, RSS), linked pages and attachments
│   ├── inbox_indexer.py      # Indexing of manually uploaded documents
│   ├── reembed.py            # Re-embeds the whole store with another model while the API keeps answering
│   ├── embed_check.py        # Checks that Ollama's vectors do not change across an upgrade
│   ├── eval.py               # RAG quality evaluation
│   ├── purge_logs.py         # Applies retention periods to user logs (daily cron)
│   ├── forget.py             # Deletes questions on request from logs and API memory
│   ├── adduser.py            # Creates/updates/removes users (--send-email: invitation with a link, no password by email)
│   ├── feedback_open.py      # Lists open negative feedback
│   ├── check_version.py      # Checks that version.json, CHANGELOG, CITATION.cff and publiccode.yml agree (CI, release)
│   ├── cleanup_index.py      # Purges crawl-cache entries now excluded by the crawler filters
│   ├── full_sync.sh          # Monthly full sync (zero downtime, sync lock)
│   ├── incremental_sync.sh   # Weekly incremental sync (sync lock)
│   ├── news_sync.sh          # News sync every 15 minutes (same lock, skips while a sync runs)
│   ├── setup_freebsd.sh      # Initial setup on FreeBSD
│   ├── setup_sync_user.sh    # Creates the unprivileged account of the sync jobs (run once as root)
│   ├── make_sync_env.py      # Builds .env.sync from an allowlist (no secrets)
│   ├── rotate_ollama_log.sh  # Rotates the Ollama logs by copy and truncate (daily cron)
│   ├── chatbot_rcd           # rc.d script for the service
│   ├── cron_setup.sh         # Installs the cron jobs of the syncs, the inbox, the log purge and log rotation (run as root; the watchdog, backup and news sync entries are added separately)
│   ├── backup_vectorstore.sh # Daily vector store backup
│   ├── watchdog.sh           # Watchdog: restarts the API if it is down or unresponsive
│   ├── newsyslog-chatbot.conf # Log rotation configuration
│   └── pf.conf.example       # Firewall template (pf)
│
└── data/                     # Auto-generated (do not commit)
    ├── crawl_cache/          # Page cache (pages/), index.json, crawl_state.json, pdf_link_texts.json, news_state.json (written by the crawler and news_sync.py)
    ├── documents/            # Downloaded PDFs
    ├── vectorstore/          # embeddings.npy, metadata.json, ids.json and manifest.json (size and SHA-256 of the three, written last); written by the sync jobs
    ├── inbox/                # Documents to index manually (processed/, errors/)
    ├── backups/              # Compressed vector store backups (backup_vectorstore.sh, last 7 days kept)
    ├── gaps.jsonl            # Unanswered or weakly answered queries (content gaps)
    ├── feedback.jsonl        # User feedback (thumbs up/down, comments, links)
    ├── resolved_negative.json # Negative feedback marked as resolved
    ├── usage.jsonl           # Per-user usage (logged-in users only); written by the API, rewritten by purge_logs and forget
    ├── answers.jsonl         # Answers given, by question id (logged-in users only)
    ├── stats.json            # Query counters, the 100 most frequent questions, response times, token and cost history (last 100 records); written by the API
    ├── users.json            # Users (password stored as scrypt hash); written by adduser.py and the API (password changes)
    └── revoked_tokens.json   # Tokens ended by logout, until they expire
```

---

[Documentation index](README.md) · [Installation](installation.md) →
