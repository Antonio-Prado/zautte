#!/bin/sh
# Chatbot setup on FreeBSD
# Run as root or with sudo

set -e

echo "=== Setup Zautte su FreeBSD ==="

# --- System packages ---
echo "[1/5] Installazione pacchetti di sistema..."
# On FreeBSD PyPI has no prebuilt wheels: pip compiles numpy, lxml, pydantic-core,
# jiter etc. This needs Rust, ninja (without it pip also builds ninja and CMake: very slow),
# pkgconf and the XML libraries for lxml.
# (pip comes with the venv: python3.11 -m venv includes ensurepip)
pkg install -y \
    python311 \
    git \
    curl \
    wget \
    rust \
    ninja \
    pkgconf \
    libxml2 \
    libxslt

# --- Ollama (local LLM) ---
echo "[2/5] Installazione Ollama..."
# Ollama supports FreeBSD through the port or the Linux binary with compat
# Check https://github.com/ollama/ollama for FreeBSD updates
if ! command -v ollama > /dev/null 2>&1; then
    echo "ATTENZIONE: Installare Ollama manualmente su FreeBSD."
    echo "Opzione 1: usa il port net/ollama se disponibile"
    echo "Opzione 2: abilita Linux compatibility e usa il binario Linux"
    echo "Vedi: https://docs.freebsd.org/en/books/handbook/linuxemu/"
fi

# --- Python environment ---
echo "[3/5] Creazione ambiente virtuale Python..."
CHATBOT_DIR="$(dirname "$(dirname "$(realpath "$0")")")"
cd "$CHATBOT_DIR"

python3.11 -m venv venv
. venv/bin/activate

pip install --upgrade pip setuptools wheel

# orjson 3.11.9 and later require Rust 1.95: with an older Rust, stay on 3.11.8
if rustc --version 2>/dev/null | awk '{split($2, v, "."); exit !(v[1] == 1 && v[2] < 95)}'; then
    echo "orjson<=3.11.8" > /tmp/zautte-constraints.txt
    pip install -r requirements.txt -c /tmp/zautte-constraints.txt
else
    pip install -r requirements.txt
fi

echo "[4/5] Creazione directory dati..."
mkdir -p data/vectorstore data/documents data/crawl_cache data/inbox

echo "[5/5] Scaricamento modelli Ollama (se disponibile)..."
if command -v ollama > /dev/null 2>&1; then
    ollama pull bge-m3              # embedding: always needed, even with Claude
    ollama pull llama3.1:8b         # local LLM (LLM_PROVIDER=ollama)
    echo "Modelli scaricati."
else
    echo "SKIP: Ollama non trovato. Scaricare il modello manualmente dopo l'installazione."
fi

echo ""
echo "=== Setup completato ==="
echo "Per avviare il backend:"
echo "  . venv/bin/activate"
echo "  uvicorn api.main:app --host 0.0.0.0 --port 8000"
