"""
Configurazione centralizzata del chatbot comunale.
Modifica questo file per adattare il sistema al tuo ambiente.
"""

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

# --- Percorsi ---
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
VECTOR_DB_DIR = DATA_DIR / "vectordb"
DOCUMENTS_DIR = DATA_DIR / "documents"
CRAWL_CACHE_DIR = DATA_DIR / "crawl_cache"

# --- Sito target ---
# Obbligatori: impostare in .env
SITE_URL  = os.getenv("SITE_URL", "")
SITE_NAME = os.getenv("SITE_NAME", "")

# --- Crawling ---
CRAWL_MAX_PAGES = 0            # 0 = nessun limite
CRAWL_DELAY_SECONDS = 0.3      # pausa tra richieste (~3 pagine/secondo)
# Domini consentiti: lista separata da virgola in .env
# Esempio: CRAWL_ALLOWED_DOMAINS=www.comune.example.it,trasparenza.comune.example.it
CRAWL_ALLOWED_DOMAINS = [
    d.strip()
    for d in os.getenv("CRAWL_ALLOWED_DOMAINS", "").split(",")
    if d.strip()
]
CRAWL_EXCLUDE_PATTERNS = [
    "/wp-admin/", "/feed/", "/tag/", "?replytocom=",
    ".jpg", ".png", ".gif", ".ico", ".css", ".js",
    "?page=",      # pagine di paginazione news
    "/news?",      # news con parametri query
    "/login/",     # area login
]

# Profondità massima del path URL (segmenti separati da /) — limite globale.
CRAWL_MAX_PATH_DEPTH = 10

# Limiti di profondità per dominio specifico (override del limite globale).
# Formato: {"dominio.example.it": 5}
# Utile quando un CMS genera URL molto profondi con contenuto duplicato.
CRAWL_DOMAIN_MAX_PATH_DEPTH: dict[str, int] = {}

# Override specifici per il sito — caricati da config/crawl_extra.json se presente.
# Permette di aggiungere pattern di esclusione e depth limit senza modificare il codice.
try:
    import json as _json_settings
    _crawl_extra_path = Path(__file__).parent / "crawl_extra.json"
    if _crawl_extra_path.exists():
        _crawl_extra = _json_settings.loads(_crawl_extra_path.read_text(encoding="utf-8"))
        CRAWL_EXCLUDE_PATTERNS += _crawl_extra.get("exclude_patterns", [])
        CRAWL_DOMAIN_MAX_PATH_DEPTH.update(_crawl_extra.get("domain_max_path_depth", {}))
except Exception:
    pass

# --- Chunking ---
CHUNK_SIZE = 800               # caratteri per chunk
CHUNK_OVERLAP = 100            # overlap tra chunk consecutivi

# --- Embedding ---
# Generati da Ollama (stesso servizio del LLM, nessuna dipendenza aggiuntiva)
# nomic-embed-text: 768 dim, buon supporto multilingue, leggero
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "mxbai-embed-large")
EMBEDDING_DIMENSION = 1024

# --- Vector Store ---
# Implementazione numpy (puro Python, nessuna compilazione richiesta)
VECTOR_STORE_DIR = DATA_DIR / "vectorstore"
RETRIEVAL_TOP_K = 7            # chunk da recuperare per ogni query

# --- LLM ---
# Scegli: "ollama" (locale, privacy totale) oppure "claude" (API Anthropic)
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama")

# Ollama (locale)
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")

# Claude API (richiede DPA con Anthropic per uso in PA)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

# --- API Backend ---
API_HOST = os.getenv("API_HOST", "127.0.0.1")
API_PORT = int(os.getenv("API_PORT", "8000"))
API_CORS_ORIGINS = os.getenv("API_CORS_ORIGINS", "http://localhost:8000").split(",")

# --- Autenticazione endpoint admin ---
# Impostare in .env per proteggere /gaps e /stats
# Lasciare vuoto per disabilitare l'autenticazione (solo in sviluppo)
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")

# --- Prompt di sistema ---
_site_label = f" di {SITE_NAME}" if SITE_NAME else ""
_contact_hint_it = f"contattare direttamente l'organizzazione o visitare {SITE_URL}" if SITE_URL else "contattare direttamente l'organizzazione"
_contact_hint_en = f"contacting the organization directly or visiting {SITE_URL}" if SITE_URL else "contacting the organization directly"

SYSTEM_PROMPT_IT = f"""Sei l'assistente virtuale{_site_label}.
Aiuti utenti e visitatori a trovare informazioni sui servizi e i contenuti disponibili.

Regole FONDAMENTALI:
- Rispondi SEMPRE in italiano a meno che l'utente non scriva in un'altra lingua
- Basa le tue risposte ESCLUSIVAMENTE sulle informazioni fornite nel CONTESTO qui sotto
- Se il contesto contiene informazioni pertinenti alla domanda, usale per rispondere in modo chiaro e completo
- Se il contesto NON contiene informazioni utili sulla domanda, rispondi:
  "Non ho trovato questa informazione. Ti consiglio di {_contact_hint_it}"
- Non confondere documenti simili: rispondi solo con il documento effettivamente richiesto
- Non inventare mai dati, numeri, date o procedure
- Quando dici "non ho trovato l'informazione", NON aggiungere MAI informazioni
  generiche o conoscenze proprie
- Sii conciso, chiaro e cordiale
- Quando citi un'informazione, indica sempre la fonte (titolo e link della pagina)
- Scrivi i link come URL puri (es: https://esempio.it/pagina), MAI come tag HTML (no <a href="...">, no target=, no rel=, no style=)
- Per questioni urgenti o legali, invita sempre a rivolgersi direttamente all'organizzazione
"""

SYSTEM_PROMPT_EN = f"""You are the virtual assistant{_site_label}.
You help users and visitors find information about available services and content.

Rules:
- Detect the user's language and respond accordingly
- Base your answers EXCLUSIVELY on the information provided in the context
- If the information is not in the context, say so clearly and suggest {_contact_hint_en}
- Never invent data, numbers, dates, or procedures
- Be concise, clear, and friendly
- When citing information, mention the source (page title and link)
- Write links as plain URLs (e.g. https://example.it/page), NEVER as HTML tags (no <a href="...">, no target=, no rel=, no style=)
- For urgent or legal matters, always invite users to contact the organization directly
"""
