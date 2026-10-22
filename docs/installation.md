<sub>[Zautte](../README.md) › [Documentation](README.md) › Installation</sub>

# Installation

Installing Zautte on FreeBSD, from the prerequisites to the first indexing.

## Prerequisites

- FreeBSD 15 (tested on 15.1-RELEASE; 14.x should also work)
- Python 3.11 (the lock file and CI use 3.11)
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

The script installs the system packages (`python311`, git, curl, wget and the build tools above), creates the virtualenv, installs `requirements-lock.txt` (with `orjson<=3.11.8` when Rust is older than 1.95), creates the `data/` directories and, if Ollama is already installed, pulls `bge-m3` and `llama3.1:8b`. Ollama itself must be installed separately.

## Manual Setup

```sh
cd /opt/chatbot

# Create and activate the virtualenv
python3.11 -m venv venv
. venv/bin/activate

# Install dependencies
pip install -r requirements-lock.txt   # exact versions, each checked against its SHA-256 (FreeBSD, Python 3.11); requirements.txt has the minimums, rank-bm25 included

# Configure the environment
cp .env.example .env
chmod 600 .env   # API keys and secrets; with a dedicated service account use root:<account> and 640 (see Operations)
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

When a sync finishes, the API has to load the new index. Run as root, `scripts.sync` restarts it (or, for `news`, asks it to reload); run as the unprivileged sync account, it leaves a request file that the root watchdog acts on within a minute (see [Watchdog](operations.md#watchdog)).

## Starting the Backend

```sh
# Development (with auto-reload)
venv/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload

# Production (via rc.d — see operations.md): create the unprivileged service account and set
# chatbot_user first (the script does not start as root), install the watchdog and the log rotation
service chatbot start
```

---

← [Architecture](architecture.md) · [Documentation index](README.md) · [Configuration](configuration.md) →
