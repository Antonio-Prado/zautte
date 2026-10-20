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

The API keeps no conversation state: the last 3 turns of a conversation are kept in the API's memory only (api/conversations.py: two hours, never on disk) and the widget holds just the conversation id. Single questions are still logged, after personal-data masking, in `data/usage.jsonl` (logged-in users only), `data/gaps.jsonl` and `data/feedback.jsonl`, and the answers given in `data/answers.jsonl` (logged-in users only), subject to the `RETENTION_*` periods and with an id (`rid`) that lets `scripts/forget.py` delete them on request; `data/stats.json` also keeps the 100 most frequent questions, outside those periods. The API log records only the question id (see [Privacy and Security](privacy-and-security.md)).

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
├── start.sh                  # Launcher run by the rc.d service (uvicorn on 0.0.0.0 and ::, port 8000)
├── LICENSE, CONTRIBUTING.md, SECURITY.md
├── .github/                  # CI (ruff lint), CodeQL, Dependabot, issue/PR templates
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
│   ├── site-footer.js        # Footer shared by the pages (latest release and commit, accessibility link, client IP)
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
│   │   api.md, widget.md, operations.md, privacy-and-security.md, releasing.md
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
│   ├── adduser.py            # Creates/updates/removes pilot users (--send-email: invitation with a link, no password by email)
│   ├── feedback_open.py      # Lists open negative feedback
│   ├── cleanup_index.py      # Purges crawl-cache entries now excluded by the crawler filters
│   ├── full_sync.sh          # Monthly full sync (zero downtime, sync lock)
│   ├── incremental_sync.sh   # Weekly incremental sync (sync lock)
│   ├── news_sync.sh          # News sync every 15 minutes (same lock, skips while a sync runs)
│   ├── setup_freebsd.sh      # Initial setup on FreeBSD
│   ├── chatbot_rcd           # rc.d script for the service
│   ├── cron_setup.sh         # Installs the cron jobs (run as root)
│   ├── backup_vectorstore.sh # Daily vector store backup
│   ├── watchdog.sh           # Watchdog: restarts the API if it is down or unresponsive
│   ├── newsyslog-chatbot.conf # Log rotation configuration
│   └── pf.conf.example       # Firewall template (pf)
│
└── data/                     # Auto-generated (do not commit)
    ├── crawl_cache/          # Page cache (pages/), index.json, crawl_state.json, pdf_link_texts.json, news_state.json
    ├── documents/            # Downloaded PDFs
    ├── vectorstore/          # Embeddings + metadata (numpy)
    ├── inbox/                # Documents to index manually (processed/, errors/)
    ├── backups/              # Compressed vector store backups
    ├── gaps.jsonl            # Unanswered or weakly answered queries (content gaps)
    ├── feedback.jsonl        # User feedback (thumbs up/down, comments, links)
    ├── resolved_negative.json # Negative feedback marked as resolved
    ├── usage.jsonl           # Per-user usage (pilot users only)
    ├── answers.jsonl         # Answers given, by question id (pilot users only)
    ├── stats.json            # Query counters, the 100 most frequent questions, response times, token and cost history
    ├── users.json            # Pilot users (password stored as scrypt hash)
    └── revoked_tokens.json   # Tokens ended by logout, until they expire
```

---

[Documentation index](README.md) · [Installation](installation.md) →
