#!/bin/sh
# Setup del chatbot su FreeBSD
# Eseguire come root o con sudo

set -e

echo "=== Setup Chatbot Comune SBT su FreeBSD ==="

# --- Pacchetti di sistema ---
echo "[1/5] Installazione pacchetti di sistema..."
# Su FreeBSD PyPI non ha wheel precompilate: pip compila numpy, lxml, pydantic-core,
# jiter ecc. Servono Rust, ninja (senza, pip compila anche ninja e CMake: lentissimo),
# pkgconf e le librerie XML per lxml.
# (pip arriva con il venv: python3.11 -m venv include ensurepip)
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

# --- Ollama (LLM locale) ---
echo "[2/5] Installazione Ollama..."
# Ollama ha supporto FreeBSD tramite il port o binario Linux con compat
# Verificare https://github.com/ollama/ollama per aggiornamenti FreeBSD
if ! command -v ollama > /dev/null 2>&1; then
    echo "ATTENZIONE: Installare Ollama manualmente su FreeBSD."
    echo "Opzione 1: usa il port net/ollama se disponibile"
    echo "Opzione 2: abilita Linux compatibility e usa il binario Linux"
    echo "Vedi: https://docs.freebsd.org/en/books/handbook/linuxemu/"
fi

# --- Ambiente Python ---
echo "[3/5] Creazione ambiente virtuale Python..."
CHATBOT_DIR="$(dirname "$(dirname "$(realpath "$0")")")"
cd "$CHATBOT_DIR"

python3.11 -m venv venv
. venv/bin/activate

pip install --upgrade pip setuptools wheel

# orjson 3.11.9 e successivi richiedono Rust 1.95: con un Rust più vecchio si resta alla 3.11.8
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
    ollama pull mxbai-embed-large   # embedding: serve sempre, anche con Claude
    ollama pull llama3.1:8b         # LLM locale (LLM_PROVIDER=ollama)
    echo "Modelli scaricati."
else
    echo "SKIP: Ollama non trovato. Scaricare il modello manualmente dopo l'installazione."
fi

echo ""
echo "=== Setup completato ==="
echo "Per avviare il backend:"
echo "  . venv/bin/activate"
echo "  uvicorn api.main:app --host 0.0.0.0 --port 8000"
