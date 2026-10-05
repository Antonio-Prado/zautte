<sub>[Zautte](../README.md) › [Documentation](README.md) › Installation</sub>

# Installation

Installing Zautte on FreeBSD, from the prerequisites to the first indexing.

## Prerequisites

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

## Automated Setup

```sh
# Clone the repository
git clone <repo_url> /opt/chatbot
cd /opt/chatbot

# Run the setup script (as root)
sh scripts/setup_freebsd.sh
```

The script installs the system packages (`python311`, git, curl, wget and the build tools above), creates the virtualenv, installs `requirements.txt` (with `orjson<=3.11.8` when Rust is older than 1.95), creates the `data/` directories and, if Ollama is already installed, pulls `bge-m3` and `llama3.1:8b`. Ollama itself must be installed separately.

## Manual Setup

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

## First Indexing

```sh
# 1. Full site crawl (no page limit by default: a large site takes hours)
venv/bin/python -m crawler.crawler

# 2. Generate embeddings and populate the vector store
venv/bin/python -m indexer.indexer

# Or in a single command (crawl + index + inbox):
venv/bin/python -m scripts.sync full
```

Every `scripts.sync` mode ends by killing the running `uvicorn api.main:app` processes, so that the API reloads the updated vector store: in production the watchdog starts the service again (see [Watchdog](operations.md#watchdog)).

## Starting the Backend

```sh
# Development (with auto-reload)
venv/bin/python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

# Production (via rc.d — see operations.md)
service chatbot start
```

---

← [Architecture](architecture.md) · [Documentation index](README.md) · [Configuration](configuration.md) →
