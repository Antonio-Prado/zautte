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

The API keeps no conversation state: turn history is managed client-side by the widget and sent with every request (max 3 turns = 6 messages), and login tokens are stateless. Single questions are still logged, after personal-data masking, in `data/usage.jsonl` (logged-in users only), `data/gaps.jsonl` and `data/feedback.jsonl`, subject to the `RETENTION_*` periods; `data/stats.json` also keeps the 100 most frequent questions and the API log the first 60–120 characters of each, outside those periods (see [Privacy and Security](privacy-and-security.md)).

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
│   ├── titles.py             # Readable titles for PDF sources (chosen at query time)
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
│   ├── zautte-logo.svg       # Zautte logo (also used at the top of the README)
│   ├── zautte-icon.svg       # "ZA" icon in the chat header
│   ├── zautte-favicon.svg    # Favicon of the pages
│   ├── zautte-icon-180.png   # apple-touch-icon
│   └── logo.png              # "Powered by" logo in the footer (not versioned: provide your own)
│
├── docs/                     # This documentation (index: docs/README.md)
│   ├── architecture.md, installation.md, configuration.md, how-it-works.md,
│   │   api.md, widget.md, operations.md, privacy-and-security.md
│   ├── images/               # Screenshots used in the README
│   ├── informativa-privacy-zautte.md  # Draft privacy notice (to be approved by the DPO)
│   └── zautte-logo-dark.svg  # White logo for GitHub's dark theme
│
├── scripts/
│   ├── sync.py               # Orchestrator: crawl + indexing (full, incremental, inbox, full-index, reembed)
│   ├── inbox_indexer.py      # Indexing of manually uploaded documents
│   ├── reembed.py            # Re-embeds the whole store with another model while the API keeps answering
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
    ├── crawl_cache/          # Page cache (pages/), index.json, crawl_state.json, pdf_link_texts.json
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

[Documentation index](README.md) · [Installation](installation.md) →
