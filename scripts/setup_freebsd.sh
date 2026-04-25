#!/bin/sh
# Setup del chatbot su FreeBSD
# Eseguire come root o con sudo

set -e

echo "=== Setup Chatbot Comune SBT su FreeBSD ==="

# --- Pacchetti di sistema ---
echo "[1/5] Installazione pacchetti di sistema..."
pkg install -y \
    python311 \
    py311-pip \
    py311-virtualenv \
    git \
    curl \
    wget

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

pip install --upgrade pip

pip install \
    fastapi \
    "uvicorn[standard]" \
    chromadb \
    sentence-transformers \
    pymupdf \
    httpx \
    beautifulsoup4 \
    lxml \
    langchain-text-splitters \
    anthropic \
    python-dotenv \
    pydantic-settings

echo "[4/5] Creazione directory dati..."
mkdir -p data/vectordb data/documents data/crawl_cache

echo "[5/5] Scaricamento modello LLM (se Ollama disponibile)..."
if command -v ollama > /dev/null 2>&1; then
    ollama pull llama3.1:8b
    echo "Modello llama3.1:8b scaricato."
else
    echo "SKIP: Ollama non trovato. Scaricare il modello manualmente dopo l'installazione."
fi

echo ""
echo "=== Setup completato ==="
echo "Per avviare il backend:"
echo "  . venv/bin/activate"
echo "  uvicorn api.main:app --host 0.0.0.0 --port 8000"
