# Zautte — Documentazione Tecnica

Zautte è un assistente virtuale RAG (Retrieval-Augmented Generation) per qualsiasi sito web. Risponde alle domande degli utenti basandosi esclusivamente sui contenuti del sito indicizzato, con supporto multilingue (italiano/inglese) e piena attenzione alla privacy (GDPR-ready).

---

## Indice

1. [Architettura](#architettura)
2. [Stack tecnologico](#stack-tecnologico)
3. [Struttura del repository](#struttura-del-repository)
4. [Installazione](#installazione)
5. [Configurazione](#configurazione)
6. [Pipeline RAG](#pipeline-rag)
7. [Crawler](#crawler)
8. [Indicizzatore](#indicizzatore)
9. [Vector Store](#vector-store)
10. [Backend API](#backend-api)
11. [Widget frontend](#widget-frontend)
12. [Operazioni periodiche](#operazioni-periodiche)
13. [Servizio di sistema (FreeBSD)](#servizio-di-sistema-freebsd)
14. [Monitoraggio e valutazione](#monitoraggio-e-valutazione)
15. [Privacy e sicurezza](#privacy-e-sicurezza)
16. [Risoluzione problemi](#risoluzione-problemi)

---

## Architettura

```
                    ┌─────────────────────────────────────────┐
                    │           Sito da indicizzare            │
                    │        il-tuo-sito.it  (HTML + PDF)      │
                    └──────────────┬──────────────────────────┘
                                   │ crawl
                                   ▼
                    ┌─────────────────────────────────────────┐
                    │             crawler/                     │
                    │  Scarica pagine, estrae testo e metadati │
                    │  Modalità: completo | incrementale       │
                    └──────────────┬──────────────────────────┘
                                   │ index.json
                                   ▼
                    ┌─────────────────────────────────────────┐
                    │             indexer/                     │
                    │  Chunking semantico → Embedding (Ollama) │
                    │  Upsert nel vector store (numpy)         │
                    └──────────────┬──────────────────────────┘
                                   │ data/vectorstore/
                                   ▼
    Utente ─── Widget JS ─── api/main.py (FastAPI)
                                   │
                              api/rag.py
                         ┌─────────┴──────────┐
                    Hybrid Search           LLM (Ollama/Claude)
                  (cosine + BM25)          Risposta in streaming
```

Il sistema è **stateless**: le conversazioni non vengono memorizzate sul server. La cronologia dei turni è gestita lato client dal widget e inviata ad ogni richiesta (max 3 turni = 6 messaggi).

---

## Stack tecnologico

| Componente        | Tecnologia                                           |
|-------------------|------------------------------------------------------|
| OS                | FreeBSD 14                                           |
| Python            | 3.11+                                                |
| Web scraping      | httpx + BeautifulSoup4/lxml                          |
| PDF parsing       | pypdf (puro Python, zero compilazione)               |
| Embedding         | Ollama (`mxbai-embed-large`, 1024 dim)               |
| Vector store      | numpy (custom, puro Python)                          |
| Keyword search    | rank-bm25 (BM25Okapi)                                |
| LLM               | Ollama locale (`qwen2.5:7b`) oppure Claude API       |
| Backend           | FastAPI + uvicorn                                    |
| Rate limiting     | slowapi                                              |
| Frontend          | Vanilla JS/CSS (zero dipendenze esterne)             |
| Supervisore       | FreeBSD rc.d + daemon(8)                             |
| Log rotation      | newsyslog                                            |

---

## Struttura del repository

```
chatbot/
├── .env.example              # Template variabili d'ambiente
├── requirements.txt          # Dipendenze Python
├── start.sh                  # Avvio rapido (sviluppo)
│
├── config/
│   └── settings.py           # Configurazione centralizzata (carica .env)
│
├── crawler/
│   ├── crawler.py            # Crawler asincrono httpx
│   └── state.py              # Stato crawl per modalità incrementale
│
├── indexer/
│   ├── chunker.py            # Chunking semantico per paragrafo
│   ├── embedder.py           # Generazione embedding via Ollama
│   ├── indexer.py            # Orchestratore: crawler output → vector store
│   ├── pdf_extractor.py      # Estrazione testo da PDF (pypdf)
│   └── vector_store.py       # Store numpy: cosine search + BM25 hybrid
│
├── api/
│   ├── main.py               # FastAPI: endpoints, rate limiting, auth admin
│   └── rag.py                # Pipeline RAG: expand → retrieve → rerank → LLM
│
├── widget/
│   ├── chatbot-widget.js     # Widget chat (JS/CSS auto-contenuto)
│   ├── embed-snippet.html    # Snippet da incollare nel sito
│   └── dashboard.html        # Pannello di controllo (area riservata)
│
├── scripts/
│   ├── sync.py               # Orchestratore: crawl + indicizzazione
│   ├── inbox_indexer.py      # Indicizzazione documenti caricati manualmente
│   ├── eval.py               # Valutazione qualità RAG
│   ├── setup_freebsd.sh      # Setup iniziale su FreeBSD
│   ├── chatbot_rcd           # Script rc.d per il servizio
│   ├── cron_setup.sh         # Configura cron job
│   ├── incremental_sync.sh   # Sync notturno incrementale
│   ├── backup_vectorstore.sh # Backup giornaliero vector store
│   ├── watchdog.sh           # Watchdog: riavvia se non risponde
│   └── newsyslog-chatbot.conf# Configurazione rotazione log
│
└── data/                     # Generata automaticamente (non committare)
    ├── crawl_cache/          # Cache pagine scaricate + index.json
    ├── documents/            # PDF scaricati
    ├── vectorstore/          # Embedding + metadata (numpy)
    ├── inbox/                # Documenti da indicizzare manualmente
    ├── backups/              # Backup compressi del vector store
    ├── gaps.jsonl            # Query senza risposta (gap di contenuto)
    └── feedback.jsonl        # Feedback utenti (pollice su/giù)
```

---

## Installazione

### Prerequisiti

- FreeBSD 14 (o compatibile)
- Python 3.11+
- [Ollama](https://ollama.com) installato e in esecuzione (`ollama serve`)
- Modelli Ollama scaricati:

```sh
ollama pull mxbai-embed-large   # embedding (1024 dim)
ollama pull qwen2.5:7b          # LLM (oppure qwen2.5:3b per meno RAM)
```

### Setup automatico

```sh
# Clona il repository
git clone <repo_url> /opt/chatbot
cd /opt/chatbot

# Esegui lo script di setup (come root)
sh scripts/setup_freebsd.sh
```

Lo script installa i pacchetti di sistema (`python311`, `py311-pip`, etc.), crea il virtualenv e installa le dipendenze Python.

### Setup manuale

```sh
cd /opt/chatbot

# Crea e attiva il virtualenv
python3.11 -m venv venv
. venv/bin/activate

# Installa dipendenze
pip install -r requirements.txt
pip install rank-bm25            # per hybrid search BM25

# Configura l'ambiente
cp .env.example .env
# Modifica .env con i tuoi valori

# Crea le directory dati
mkdir -p data/vectorstore data/documents data/crawl_cache data/inbox
```

### Prima indicizzazione

```sh
# 1. Crawl completo del sito (~6000 pagine, richiede ore)
venv/bin/python -m crawler.crawler

# 2. Genera embedding e popola il vector store
venv/bin/python -m indexer.indexer

# Oppure in un solo comando (crawl + indice + inbox):
venv/bin/python -m scripts.sync full
```

### Avvio del backend

```sh
# Sviluppo (con reload automatico)
venv/bin/python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

# Produzione (tramite rc.d — vedi sezione dedicata)
service chatbot start
```

---

## Configurazione

Tutta la configurazione è in `config/settings.py`, che carica automaticamente le variabili da `.env` tramite `python-dotenv`.

### File `.env`

```ini
# Provider LLM: "ollama" (locale) oppure "claude" (API Anthropic)
LLM_PROVIDER=ollama

# Ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:7b
OLLAMA_EMBED_MODEL=mxbai-embed-large

# Claude API (richiede DPA con Anthropic per uso in PA)
ANTHROPIC_API_KEY=

# Backend
API_HOST=127.0.0.1
API_PORT=8000
API_CORS_ORIGINS=https://www.tuo-sito.it

# Chiave admin per endpoint protetti (/stats, /gaps, /feedback/list)
# Lasciare vuoto per disabilitare l'autenticazione (solo sviluppo)
ADMIN_API_KEY=chiave-segreta-qui
```

### Parametri principali in `settings.py`

| Parametro              | Default                        | Descrizione                                          |
|------------------------|--------------------------------|------------------------------------------------------|
| `SITE_URL`             | *(da .env)*                    | URL radice per il crawl                              |
| `CRAWL_MAX_PAGES`      | `10000`                        | Limite massimo pagine da scansionare                 |
| `CRAWL_DELAY_SECONDS`  | `1.0`                          | Pausa tra richieste (rispetto del server)            |
| `CRAWL_ALLOWED_DOMAINS`| *(da .env)*                    | Domini consentiti nel crawl                          |
| `CRAWL_EXCLUDE_PATTERNS`| Liste pattern da escludere    | URL da ignorare (admin, feed, immagini, etc.)        |
| `CHUNK_SIZE`           | `800`                          | Dimensione target chunk (caratteri)                  |
| `CHUNK_OVERLAP`        | `100`                          | Overlap tra chunk (minimo — effettivo è 150)         |
| `OLLAMA_EMBED_MODEL`   | `mxbai-embed-large`            | Modello embedding (1024 dim)                         |
| `EMBEDDING_DIMENSION`  | `1024`                         | Dimensione vettori embedding                         |
| `RETRIEVAL_TOP_K`      | `5`                            | Chunk da recuperare per query                        |
| `LLM_PROVIDER`         | `ollama`                       | `ollama` oppure `claude`                             |
| `OLLAMA_MODEL`         | `llama3.1:8b`                  | Modello LLM locale                                   |
| `CLAUDE_MODEL`         | `claude-sonnet-4-6`            | Modello Claude API                                   |

---

## Pipeline RAG

Ogni domanda percorre questa pipeline in `api/rag.py`:

```
Domanda utente
     │
     ▼
1. expand_query()        — aggiunge sinonimi dal dominio (es. "TARI" → "tassa rifiuti")
     │
     ▼
2. embed_query()         — vettorizza la query espansa (Ollama mxbai-embed-large)
     │
     ▼
3. hybrid_search()       — cosine similarity (60%) + BM25 (40%) con RRF
     │
     ▼
4. filtro MIN_SIMILARITY — scarta chunk con score < 0.45
     │
     ▼
5. rerank()              — boost titolo, boost categoria "servizio", penalità duplicati
     │
     ▼
6. build_context_block() — formatta chunk con metadati (categoria, stato, data)
     │
     ▼
7. detect_language()     — rileva italiano vs inglese (euristica keyword)
     │
     ▼
8. build_prompt()        — system prompt + history conversazionale + contesto + domanda
     │
     ▼
9. LLM (Ollama/Claude)   — genera risposta (streaming o completa)
     │
     ▼
Risposta + fonti
```

### Query expansion

`expand_query()` cerca termini chiave del dominio nella query e li arricchisce con sinonimi predefiniti (configurabili in `config/synonyms.json`). Esempio:

- `"carta identità"` → aggiunge `"documento identità CIE carta d'identità elettronica"`
- `"TARI"` → aggiunge `"tassa rifiuti raccolta rifiuti"`
- `"SUE"` → aggiunge `"sportello edilizia permesso costruire concessione"`

### Re-ranking

`rerank()` aggiusta gli score dei chunk senza usare un modello aggiuntivo:

- **+0.02 per ogni termine della query** presente nel titolo del chunk
- **+0.01** se la categoria è `"servizio"` (più utile per l'utente)
- **-0.05 × n** se lo stesso source è già apparso (penalizza duplicati)

### Suggerimento uffici

Quando non viene trovato nessun chunk rilevante (`chunks == 0`), `suggest_office()` analizza la query e suggerisce il contatto competente con il link diretto. Gli uffici/contatti sono configurabili in `config/offices.json`.

### Cache risposte

Le risposte a query identiche (senza history conversazionale) vengono cachate in memoria con una cache LRU da 200 voci. La chiave di cache è l'hash MD5 della query normalizzata (lowercase, strip).

### Gap log

Ogni query che produce 0 chunk viene registrata in `data/gaps.jsonl` (timestamp + testo troncato a 200 char, senza dati personali). Consultabile via endpoint admin `GET /gaps`.

---

## Crawler

`crawler/crawler.py` — crawler asincrono basato su httpx e BeautifulSoup.

### Funzionamento

1. **BFS** (breadth-first) a partire da `SITE_URL`
2. Segue solo link verso i domini in `CRAWL_ALLOWED_DOMAINS`
3. Salta URL corrispondenti a `CRAWL_EXCLUDE_PATTERNS`
4. Per ogni pagina HTML:
   - Estrae il testo con `clean_text()` (rimuove nav, footer, widget, righe rumore)
   - Estrae il titolo con `extract_title()`
   - Estrae metadati con `extract_metadata()` (categoria, sezione, data, stato servizio)
   - Salva un `.json` nella cartella `data/crawl_cache/pages/`
5. Scarica i PDF trovati nelle pagine
6. Aggiorna lo stato in `data/crawl_cache/crawl_state.json`
7. Salva l'indice in `data/crawl_cache/index.json`

### Modalità incrementale

```sh
python -m crawler.crawler --incremental
```

In modalità incrementale il crawler:
- Carica l'indice esistente come base
- Confronta l'hash MD5 del contenuto di ogni pagina con quello memorizzato
- Salta le pagine non modificate (molto più veloce)
- Rimuove dall'indice le pagine scomparse dal sito

Lo stato è gestito dalla classe `CrawlState` in `crawler/state.py`.

### Estrazione metadati

`extract_metadata(html, url)` restituisce:

| Campo            | Come viene determinato                                   |
|------------------|----------------------------------------------------------|
| `category`       | Pattern nell'URL (`/services/` → `servizio`, etc.)      |
| `section`        | Sottodominio (`amministrazionetrasparente` → `trasparenza`) |
| `date`           | Meta tag `article:modified_time`, `date`, o `<time>`    |
| `service_status` | Testo "servizio attivo/non attivo" nella pagina          |

### Pulizia testo

`clean_text()` rimuove:
- Tag `<script>`, `<style>`, `<nav>`, `<header>`, `<footer>`, `<aside>`, `<form>`, `<iframe>`
- Elementi CSS con classi `feedback`, `rating`, `survey`, `cookie`, `breadcrumb`, `pagination`, etc.
- Righe rumorose: "vai alla pagina", "leggi di più", "condividi", "stampa", numeri di pagina, etc.
- Righe più corte di 4 caratteri

---

## Indicizzatore

### Chunking semantico (`indexer/chunker.py`)

La divisione in chunk usa una strategia semantica a due livelli:

1. **Split per paragrafo** (`\n\n`): mantiene unità di senso intere
2. **Se il paragrafo supera 1200 caratteri**: ulteriore divisione con `RecursiveCharacterTextSplitter` e overlap di 150 caratteri

Per ogni chunk:
- Si prepone il **titolo della pagina** (migliora il retrieval semantico)
- Si rimuovono righe rumorose (navigazione, "accedi al servizio", "con SPID", etc.)
- Si scartano chunk con meno di **80 caratteri**

I metadati di ogni chunk includono: `source` (URL), `title`, `doc_type` (`html`/`pdf`), `chunk_index`, `chunk_total`, più i metadati aggiuntivi dalla pagina (`category`, `section`, `date`, `service_status`).

### Embedding (`indexer/embedder.py`)

Gli embedding sono generati via Ollama usando il modello `mxbai-embed-large` (1024 dimensioni, buon supporto multilingue).

- Usa l'endpoint `/api/embed` di Ollama con **batch nativi** (32 testi per chiamata)
- Fallback automatico all'endpoint `/api/embeddings` (uno alla volta) se il batch fallisce
- `embed_query()` per le query utente (singola chiamata)

### Indicizzatore principale (`indexer/indexer.py`)

```sh
python -m indexer.indexer              # indicizza tutto
python -m indexer.indexer --reset      # svuota e reindicizza
python -m indexer.indexer --stats      # mostra statistiche
python -m indexer.indexer --only-html  # solo pagine HTML
python -m indexer.indexer --only-pdf   # solo PDF
```

Legge `data/crawl_cache/index.json` e processa in batch da 50 chunk alla volta (embedding + upsert nel vector store).

### Inbox documenti (`scripts/inbox_indexer.py`)

Permette di indicizzare documenti caricati manualmente:

```sh
# Deposita i file in:
data/inbox/delibera.pdf
data/inbox/delibera.json   # metadati opzionali

# Processa manualmente:
python -m scripts.inbox_indexer

# Oppure in modalità watch (polling ogni 60s):
python -m scripts.inbox_indexer --watch
```

**Formati supportati**: PDF, TXT, DOCX

**Metadati opzionali** (file `.json` accanto al documento):
```json
{
    "title": "Delibera n.15 del 2024",
    "source_url": "https://www.mioente.it/documenti/2024/15",
    "category": "delibere"
}
```

I file processati vengono spostati in `data/inbox/processed/` (oppure `data/inbox/errors/` in caso di errore).

---

## Vector Store

`indexer/vector_store.py` — implementazione numpy, zero dipendenze da compilare.

### Struttura dati

Tre file sul disco:

| File                          | Contenuto                        |
|-------------------------------|----------------------------------|
| `data/vectorstore/embeddings.npy` | Matrice numpy (N × 1024) float32 |
| `data/vectorstore/metadata.json`  | Lista di dict con metadati chunk |
| `data/vectorstore/ids.json`       | Lista di ID (hash MD5)           |

### Ricerca coseno

I vettori vengono normalizzati all'inserimento. La ricerca è un semplice prodotto matriciale:

```python
scores = _embeddings @ query_vector   # cosine similarity
```

### Hybrid search (BM25 + vettoriale)

`hybrid_search()` combina i due ranking tramite **Reciprocal Rank Fusion (RRF)**:

```
score_finale(doc) = 0.6 × RRF_vettoriale(doc) + 0.4 × RRF_bm25(doc)
RRF(rank) = 1 / (60 + rank + 1)
```

L'indice BM25 viene costruito in memoria al primo accesso dopo ogni salvataggio. Richiede il pacchetto `rank-bm25`.

### Upsert idempotente

`upsert_chunks()` identifica ogni chunk con un hash MD5 di `source_url + chunk_index + testo[:64]`. Se il chunk esiste già, aggiorna l'embedding in place; altrimenti lo aggiunge. Questo rende l'operazione sicura da eseguire più volte.

### Nota operativa

Il vector store è caricato **una volta sola** in memoria all'avvio del processo API. Nuovi chunk aggiunti dall'indicizzatore mentre l'API è in esecuzione non sono visibili fino al riavvio del servizio.

---

## Backend API

`api/main.py` — FastAPI con streaming SSE, rate limiting e autenticazione admin.

### Avvio

```sh
# Sviluppo
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

# Produzione (2 worker)
uvicorn api.main:app --host 127.0.0.1 --port 8000 --workers 2
```

### Endpoints

#### `GET /health`

Stato del servizio. Non richiede autenticazione.

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

Risposta completa (non streaming). Attende la risposta intera prima di rispondere.

**Rate limit**: 20 richieste/ora per IP; 200 richieste/ora globali.

**Request:**
```json
{
  "question": "Come si richiede la carta di identità?",
  "history": [
    {"role": "user", "content": "Dove si trova l'anagrafe?"},
    {"role": "assistant", "content": "L'ufficio anagrafe si trova in..."}
  ]
}
```

- `question`: stringa 1–1000 caratteri
- `history`: opzionale, max 6 messaggi (3 turni)

**Response:**
```json
{
  "answer": "La carta di identità si richiede presso l'Ufficio Anagrafe...",
  "sources": [
    {"title": "Carta di Identità Elettronica", "url": "https://...", "score": 0.87}
  ],
  "language": "it"
}
```

---

#### `POST /chat/stream`

Risposta in streaming via **Server-Sent Events (SSE)**.

**Rate limit**: identico a `/chat`.

**Request**: identica a `/chat`.

**Stream di eventi:**
```
data: {"token": "La "}
data: {"token": "carta "}
data: {"token": "di "}
...
data: {"sources": [{"title": "...", "url": "...", "score": 0.87}]}
data: {"done": true}
```

In caso di errore durante lo stream:
```
data: {"error": "Errore durante la generazione"}
```

---

#### `POST /feedback`

Salva il feedback dell'utente (pollice su/giù). Nessun dato personale memorizzato.

**Rate limit**: 60 richieste/ora per IP.

**Request:**
```json
{
  "question": "Come si richiede la carta di identità?",
  "answer": "La carta di identità si richiede...",
  "rating": 1
}
```

- `rating`: `-1` (negativo) oppure `1` (positivo)

---

#### `GET /stats` *(admin)*

Statistiche sul vector store.

```
Headers: X-Admin-Key: <ADMIN_API_KEY>
```

```json
{"collection": "numpy_store", "total_chunks": 5420}
```

---

#### `GET /gaps?limit=50` *(admin)*

Ultime query senza risposta (gap di contenuto da colmare).

```json
{
  "gaps": [
    {"ts": "2026-04-08T10:23:00", "query": "orari biblioteca", "chunks": 0}
  ],
  "total": 42
}
```

---

#### `GET /feedback/list?limit=100` *(admin)*

Lista feedback ricevuti con conteggio positivi/negativi.

```json
{
  "feedback": [...],
  "total": 156,
  "positive": 134,
  "negative": 22
}
```

---

### Autenticazione admin

Gli endpoint `/stats`, `/gaps` e `/feedback/list` richiedono l'header `X-Admin-Key` con il valore di `ADMIN_API_KEY` dal file `.env`.

Se `ADMIN_API_KEY` è vuoto, l'autenticazione è disabilitata (solo per sviluppo).

### CORS

Il middleware CORS è configurato con `API_CORS_ORIGINS` (default: `http://localhost:8000`). In produzione impostare il dominio esatto del sito. Metodi consentiti: `GET`, `POST`, `OPTIONS`.

### Graceful shutdown

Il backend intercetta `SIGTERM` e attende 2 secondi prima di terminare, per permettere il completamento delle risposte streaming in corso.

### Widget statico

Il file `widget/chatbot-widget.js` è servito come file statico da FastAPI al percorso `/widget/chatbot-widget.js`.

---

## Widget frontend

`widget/chatbot-widget.js` — widget chat auto-contenuto (JS + CSS iniettato, zero dipendenze).

### Integrazione nel sito

Incollare prima del `</body>` in tutte le pagine (o nel template del CMS):

```html
<script>
  window.ChatbotConfig = {
    apiUrl:       'https://chatbot.mioente.it',
    primaryColor: '#003366',
    title:        'Zautte',
    subtitle:     'Il Mio Ente',
    position:     'right',
  };
</script>
<script src="https://chatbot.mioente.it/widget/chatbot-widget.js" defer></script>
```

Il file `widget/embed-snippet.html` contiene lo snippet pronto da incollare.

### Opzioni di configurazione

| Opzione        | Default          | Descrizione                              |
|----------------|------------------|------------------------------------------|
| `apiUrl`       | *(obbligatorio)* | URL base del backend API                 |
| `primaryColor` | `#003366`        | Colore principale del widget             |
| `title`        | `'Assistente Virtuale'` | Nome dell'assistente                |
| `subtitle`     | `''`             | Sottotitolo nella testata del pannello   |
| `position`     | `'right'`        | `'right'` oppure `'left'`               |
| `lang`         | `navigator.language` | Lingua forzata (`'it'`, `'en'`, etc.) |

### Funzionalità

- **Streaming SSE**: i token arrivano progressivamente, il cursore lampeggia durante la generazione
- **Cronologia conversazionale**: mantiene gli ultimi 3 turni (6 messaggi) e li invia ad ogni richiesta
- **Domande suggerite**: chip cliccabili all'avvio del pannello per guidare l'utente
- **Feedback**: bottoni pollice su/giù dopo ogni risposta, inviati a `POST /feedback`
- **Hint d'attesa**: dopo 10 secondi senza risposta compare il testo "Sto elaborando…"
- **Accessibilità**: `aria-hidden`, `aria-expanded`, gestione focus all'apertura/chiusura
- **Mobile**: `font-size: 16px` sull'input (previene lo zoom automatico su iOS)
- **Tasto Chiudi**: `×` in alto a destra, devolve il focus al pulsante di apertura

### Test locale

Aprire `widget/dashboard.html` nel browser. Richiede che il backend sia raggiungibile all'indirizzo configurato in `apiUrl`.

---

## Operazioni periodiche

### Orchestratore `scripts/sync.py`

Comando unico per coordinare crawl + indicizzazione:

```sh
# Prima installazione (crawl completo + indice da zero)
python -m scripts.sync full

# Aggiornamento notturno (solo modifiche)
python -m scripts.sync incremental

# Processa solo la cartella inbox
python -m scripts.sync inbox

# Reindicizza senza nuovo crawl (dopo cambio configurazione chunking)
python -m scripts.sync full-index
```

### Cron job

Configurare con:

```sh
sh scripts/cron_setup.sh
```

Schedule installato (utente `chatbot`):

| Orario                       | Comando                         | Descrizione                     |
|------------------------------|---------------------------------|---------------------------------|
| 02:30 ogni notte             | `sync incremental`              | Aggiorna solo le modifiche      |
| 03:00 ogni domenica          | `sync full`                     | Scansione completa settimanale  |
| ogni 30 min (8–18, lun–ven)  | `sync inbox`                    | Processa documenti inbox        |

I log sono scritti in `/var/log/chatbot/`.

### `/etc/crontab` (root) — watchdog e backup

```
* * * * * root /opt/chatbot/scripts/watchdog.sh
0 2 * * * root /opt/chatbot/scripts/backup_vectorstore.sh
```

### Backup vector store

`scripts/backup_vectorstore.sh` crea ogni notte alle 02:00 un archivio compresso:

```
data/backups/vectorstore_20260408_020000.tar.gz
```

Conserva gli ultimi **7 giorni** di backup, rimuove i più vecchi automaticamente.

**Ripristino:**

```sh
cd /opt/chatbot
tar -xzf data/backups/vectorstore_YYYYMMDD_HHMMSS.tar.gz -C data/
service chatbot restart
```

### Watchdog

`scripts/watchdog.sh` viene eseguito ogni minuto da cron. Se il backend non risponde a `GET /health`, lo riavvia tramite `service chatbot start`.

---

## Servizio di sistema (FreeBSD)

### Installazione

```sh
# Copia lo script rc.d
cp /opt/chatbot/scripts/chatbot_rcd /usr/local/etc/rc.d/chatbot
chmod +x /usr/local/etc/rc.d/chatbot

# Abilita il servizio
echo 'chatbot_enable="YES"' >> /etc/rc.conf

# Avvia
service chatbot start
```

### Comandi di gestione

```sh
service chatbot start    # avvia
service chatbot stop     # ferma
service chatbot restart  # riavvia
service chatbot status   # stato
```

Lo script `scripts/chatbot_rcd` usa `daemon(8)` per:
- Scrivere il PID in `/var/run/chatbot.pid`
- Redirigere stdout/stderr in `/var/log/chatbot.log`
- Riavviare automaticamente in caso di crash (`-r`)
- Caricare le variabili d'ambiente da `.env` prima dell'avvio
- Avviare uvicorn con 2 worker

### Rotazione log

Copiare la configurazione newsyslog:

```sh
cp /opt/chatbot/scripts/newsyslog-chatbot.conf /etc/newsyslog.conf.d/chatbot.conf
```

Rotazione configurata:

| File                      | Rotazioni | Dimensione max | Compressione |
|---------------------------|-----------|----------------|--------------|
| `/var/log/chatbot.log`    | 7         | 10 MB          | gzip (J)     |
| `/var/log/chatbot-sync.log` | 7       | 5 MB           | gzip (J)     |

---

## Monitoraggio e valutazione

### Health check

```sh
curl http://127.0.0.1:8000/health
```

### Statistiche vector store (admin)

```sh
curl -H "X-Admin-Key: <chiave>" http://127.0.0.1:8000/stats
```

### Gap di contenuto (admin)

```sh
curl -H "X-Admin-Key: <chiave>" http://127.0.0.1:8000/gaps
```

Mostra le ultime domande senza risposta. Usarle per identificare argomenti da aggiungere al sito o documenti da caricare nell'inbox.

### Script di valutazione automatica

`scripts/eval.py` esegue 10 domande di test e misura retrieval e qualità delle risposte:

```sh
# Solo retrieval (più veloce)
venv/bin/python -m scripts.eval --no-llm

# Retrieval + risposta LLM completa
venv/bin/python -m scripts.eval
```

**Metriche misurate:**

| Metrica              | Descrizione                                              |
|----------------------|----------------------------------------------------------|
| Retrieval OK         | Domande per cui vengono trovati chunk sufficienti        |
| Keyword score        | % parole chiave attese presenti nella risposta           |
| Tempo medio          | Millisecondi per risposta (end-to-end)                   |

I risultati vengono salvati in `data/eval_results.json`.

**Domande di test incluse (sostituibili con domande specifiche del proprio sito):** carta di identità, cambio residenza, trasporto scolastico, accesso agli atti, orari biblioteca, TARI, permesso di costruire, polizia municipale, asilo nido.

---

## Privacy e sicurezza

### GDPR

- **Nessuna conversazione memorizzata**: il backend è stateless. La cronologia viene gestita interamente lato client dal widget.
- **Gap log**: registra solo il testo della query (troncato a 200 caratteri) e il timestamp, senza dati di sessione o IP.
- **Feedback**: registra rating, anteprima domanda e risposta (troncate), senza identificativi utente.
- **Uso di Ollama locale**: nessun dato inviato a server esterni; tutto rimane nel server locale.
- **Uso di Claude API**: richiede la stipula di un DPA (Data Processing Agreement) con Anthropic. Per enti pubblici italiani verificare i requisiti normativi applicabili.

### Rate limiting

- `/chat` e `/chat/stream`: **20 richieste/ora per IP**, **200/ora globali**
- `/feedback`: **60 richieste/ora per IP**

### Endpoint admin protetti

`/stats`, `/gaps`, `/feedback/list` richiedono l'header `X-Admin-Key`. Impostare `ADMIN_API_KEY` in `.env` in produzione.

### CORS

Configurato per accettare richieste solo dagli origin in `API_CORS_ORIGINS`. In produzione impostare il dominio esatto del sito.

### Reverse proxy (raccomandato)

In produzione far girare il backend in ascolto su `127.0.0.1:8000` e usare nginx o caddy come reverse proxy con TLS. Il backend non gestisce HTTPS direttamente. Impostare l'header `X-Accel-Buffering: no` in nginx per lo streaming SSE.

---

## Risoluzione problemi

### Il backend non risponde

```sh
service chatbot status
tail -f /var/log/chatbot.log
curl http://127.0.0.1:8000/health
```

### Vector store vuoto (chunks=0 in tutte le risposte)

```sh
# Verifica quanti chunk sono indicizzati
curl http://127.0.0.1:8000/health   # → indexed_chunks

# Se 0: eseguire l'indicizzazione
venv/bin/python -m indexer.indexer --stats
venv/bin/python -m scripts.sync full
```

### Ollama non raggiungibile

```sh
ollama list                      # verifica modelli disponibili
curl http://localhost:11434/api/tags
# Se Ollama non risponde:
service ollama start             # o il comando equivalente su FreeBSD
```

### Cambio modello embedding

Se si cambia `OLLAMA_EMBED_MODEL` è necessario svuotare e reindicizzare il vector store (dimensioni embedding incompatibili):

```sh
rm -rf data/vectorstore/
venv/bin/python -m scripts.sync full
```

### Cambio configurazione chunking

Se si modificano i parametri di chunking e si vuole applicarli a tutto il corpus già scaricato:

```sh
venv/bin/python -m scripts.sync full-index
```

Non effettua un nuovo crawl: usa i file già in `data/crawl_cache/`.

### Riavvio manuale senza fermare l'indicizzatore

```sh
# Trova il PID del processo uvicorn (non del daemon wrapper)
pgrep -f "uvicorn api.main"
kill <PID>
# Il daemon lo riavvia automaticamente se chatbot_enable="YES"
```

### Email cron con "Permission denied"

```sh
chmod +x /opt/chatbot/scripts/watchdog.sh
chmod +x /opt/chatbot/scripts/backup_vectorstore.sh
chmod +x /opt/chatbot/scripts/incremental_sync.sh
```

---

*Documentazione aggiornata: aprile 2026*
