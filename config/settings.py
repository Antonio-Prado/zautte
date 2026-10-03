"""
Configurazione centralizzata di Zautte.
Modifica questo file per adattare il sistema al tuo ambiente.
"""

import datetime as _datetime
import logging
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    # python-dotenv è facoltativo: senza, valgono solo le variabili d'ambiente
    pass


def now_local() -> _datetime.datetime:
    """Ora corrente del server con fuso locale (timezone-aware).
    Usata per tutti i timestamp registrati nei file di dati."""
    return _datetime.datetime.now(_datetime.UTC).astimezone()


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
CRAWL_EXTRA_START_URLS: list[str] = []

# Override specifici per il sito — caricati da config/crawl_extra.json se presente.
# Permette di aggiungere pattern di esclusione e depth limit senza modificare il codice.
try:
    import json as _json_settings
    _crawl_extra_path = Path(__file__).parent / "crawl_extra.json"
    if _crawl_extra_path.exists():
        _crawl_extra = _json_settings.loads(_crawl_extra_path.read_text(encoding="utf-8"))
        CRAWL_EXCLUDE_PATTERNS += _crawl_extra.get("exclude_patterns", [])
        CRAWL_DOMAIN_MAX_PATH_DEPTH.update(_crawl_extra.get("domain_max_path_depth", {}))
        CRAWL_EXTRA_START_URLS += _crawl_extra.get("extra_start_urls", [])
except (OSError, ValueError, TypeError, AttributeError) as _e:
    logging.getLogger(__name__).warning("crawl_extra.json non caricato: %s", _e)

# --- Domini migrati / dismessi ---
# Domini le cui risorse non sono più raggiungibili al vecchio indirizzo perché
# il portale è stato sostituito da una nuova piattaforma che redirige tutto a
# una landing generica (es. Amministrazione Trasparente SBT → Nuvola Palitalsoft).
# I chunk già indicizzati da questi domini RESTANO nel vector store — il testo è
# ancora valido e utile — ma:
#   - il loro URL-fonte NON viene mostrato all'utente né passato al LLM: darebbe
#     un link morto. Il documento viene citato solo per titolo/sezione.
#   - il full sync NON li tratta come "stale": non vanno rimossi solo perché non
#     più crawlabili (altrimenti si perderebbe gran parte della base di conoscenza).
# Gli URL originali restano nei metadati: se in futuro il fornitore fornirà una
# mappa di redirect stabile, si potranno rimappare. Lista separata da virgola in
# .env (override); default: portale trasparenza SBT dismesso.
MIGRATED_DOMAINS = [
    d.strip()
    for d in os.getenv(
        "MIGRATED_DOMAINS",
        # 1) vecchio portale trasparenza (redirige tutto a una landing generica)
        # 2) ponte /dati/trasparenza-legacy sul nuovo dominio (ora serve HTML generico)
        "amministrazionetrasparente.comunesbt.it,"
        "sanbenedettodeltronto.nuvolapalitalsoft.it/dati/trasparenza-legacy",
    ).split(",")
    if d.strip()
]


def is_migrated_source(url: str) -> bool:
    """True se l'URL appartiene a un dominio migrato/dismesso (link non più valido)."""
    return bool(url) and any(d in url for d in MIGRATED_DOMAINS)


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
# Scegli: "ollama" (locale, privacy totale), "claude" (API diretta Anthropic)
# oppure "bedrock" (modelli Claude su AWS Bedrock, elaborazione in UE)
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama")

# Ollama (locale)
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")

# Claude API (richiede DPA con Anthropic per uso in PA)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
# Sforzo di ragionamento per i modelli che ragionano per default (Sonnet 5.x e
# successivi): "low" tiene bassi tempi e costi, come serve a un assistente informativo.
LLM_EFFORT = os.getenv("LLM_EFFORT", "low")

# Riscrittura della domanda per il retrieval nei turni successivi al primo:
# il retrieval usa solo il testo della domanda corrente, quindi un follow-up
# come "quali sono i requisiti?" perde l'argomento del turno precedente.
# Con QUERY_REWRITE attivo la domanda viene resa autonoma dal modello prima
# della ricerca (solo quando c'è storia conversazionale). Il modello per la
# riscrittura è per default lo stesso della risposta; se ne può indicare uno
# più economico in .env (es. claude-haiku-4-5).
QUERY_REWRITE = os.getenv("QUERY_REWRITE", "true").strip().lower() in ("1", "true", "yes", "on")
CLAUDE_REWRITE_MODEL = os.getenv("CLAUDE_REWRITE_MODEL", CLAUDE_MODEL)

# Claude su AWS Bedrock (LLM_PROVIDER=bedrock; richiede `pip install "anthropic[bedrock]"`).
# Il profilo di inferenza "eu." elabora le richieste solo in regioni AWS dell'UE;
# chiamando da Milano (eu-south-1) le destinazioni sono Milano, Francoforte,
# Parigi, Irlanda, Spagna e Stoccolma. Credenziali dalla catena standard AWS
# (AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY, AWS_PROFILE o AWS_BEARER_TOKEN_BEDROCK).
BEDROCK_AWS_REGION = os.getenv("BEDROCK_AWS_REGION", "eu-south-1")
BEDROCK_MODEL = os.getenv("BEDROCK_MODEL", "eu.anthropic.claude-sonnet-4-6")
BEDROCK_REWRITE_MODEL = os.getenv("BEDROCK_REWRITE_MODEL", BEDROCK_MODEL)

# --- Dati personali nelle domande (vedi api/pii.py) ---
# Con PII_REDACTION attivo codice fiscale, IBAN, carte di pagamento, email e
# telefoni vengono sostituiti da un segnaposto prima dell'invio al modello e
# della registrazione nei log. PII_KEEP_EMAIL_DOMAINS: domini istituzionali le
# cui email restano in chiaro (es. "comune.example.it,pec.example.it").
PII_REDACTION = os.getenv("PII_REDACTION", "true").strip().lower() in ("1", "true", "yes", "on")
PII_KEEP_EMAIL_DOMAINS = [
    d.strip().lower().lstrip("@") for d in os.getenv("PII_KEEP_EMAIL_DOMAINS", "").split(",") if d.strip()
]

# --- Conservazione dei log (giorni; 0 = nessuna scadenza) ---
# Applicata da scripts/purge_logs.py (cron giornaliero). Valori da concordare con
# il DPO e riportare nell'informativa privacy.
#   RETENTION_USAGE_TEXT_DAYS: testo delle domande in usage.jsonl (poi restano
#                              solo data, utente e metriche)
#   RETENTION_USAGE_DAYS:      voci di usage.jsonl
#   RETENTION_GAPS_DAYS:       domande senza risposta in gaps.jsonl
#   RETENTION_FEEDBACK_DAYS:   feedback (voto, domanda, commento, autore)
RETENTION_USAGE_TEXT_DAYS = int(os.getenv("RETENTION_USAGE_TEXT_DAYS", "90"))
RETENTION_USAGE_DAYS = int(os.getenv("RETENTION_USAGE_DAYS", "365"))
RETENTION_GAPS_DAYS = int(os.getenv("RETENTION_GAPS_DAYS", "180"))
RETENTION_FEEDBACK_DAYS = int(os.getenv("RETENTION_FEEDBACK_DAYS", "365"))

# --- API Backend ---
API_HOST = os.getenv("API_HOST", "127.0.0.1")
API_PORT = int(os.getenv("API_PORT", "8000"))
API_CORS_ORIGINS = os.getenv("API_CORS_ORIGINS", "http://localhost:8000").split(",")

# --- Autenticazione endpoint admin ---
# Impostare in .env per proteggere /gaps e /stats
# Lasciare vuoto per disabilitare l'autenticazione (solo in sviluppo)
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")

# --- Autenticazione utenti (login email+password per pilota a gruppo ristretto) ---
# AUTH_ENABLED=true attiva il gate su /chat, /chat/stream, /feedback: chi non
# ha effettuato il login non può usare il bot. Serve per capire chi lo usa,
# quando e quanto (vedi data/usage.jsonl e GET /usage/summary).
# AUTH_SECRET: segreto per firmare i token di sessione (OBBLIGATORIO se attivo).
#   Genera con:  python -c "import secrets; print(secrets.token_hex(32))"
# Gli utenti si creano con:  python -m scripts.adduser --email ... --name "..."
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")
AUTH_SECRET = os.getenv("AUTH_SECRET", "")
AUTH_TOKEN_TTL_DAYS = int(os.getenv("AUTH_TOKEN_TTL_DAYS", "30"))
# users.json vive in data/ (già in .gitignore): credenziali fuori dal versionamento
USERS_FILE = DATA_DIR / "users.json"

# --- Invio email (SMTP) — per recapitare le credenziali ai partecipanti ---
# Relay interno del Comune (destinatari @comunesbt.it):
#   SMTP_HOST=mail.comunesbt.it  SMTP_PORT=25  (senza auth né TLS)
# SMTP_USER/PASSWORD e SMTP_STARTTLS servono solo se il relay li richiede.
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "25"))
SMTP_FROM = os.getenv("SMTP_FROM", "")
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_STARTTLS = os.getenv("SMTP_STARTTLS", "false").strip().lower() in ("1", "true", "yes", "on")
# URL della pagina dove i partecipanti fanno il login (inserito nell'email)
PILOT_LOGIN_URL = os.getenv("PILOT_LOGIN_URL", "")

# Indirizzo che riceve un'email per ogni segnalazione dal widget (👎 con
# commento e/o link). Vuoto = nessuna notifica; la segnalazione resta
# comunque visibile nella dashboard (vista amministratore).
FEEDBACK_NOTIFY_EMAIL = os.getenv("FEEDBACK_NOTIFY_EMAIL", "").strip()

# --- Prompt di sistema ---
_site_label = f" di {SITE_NAME}" if SITE_NAME else ""
# Chi contattare quando manca l'informazione ("il Comune di …" per i siti comunali)
_org_it = (f"il {SITE_NAME}" if SITE_NAME.lower().startswith("comune ") else SITE_NAME) or "l'ente"
_org_en = SITE_NAME or "the organization"
_contact_hint_it = f"contattare direttamente {_org_it}" + (f" o visitare {SITE_URL}" if SITE_URL else "")
_contact_hint_en = f"contacting {_org_en} directly" + (f" or visiting {SITE_URL}" if SITE_URL else "")

SYSTEM_PROMPT_IT = f"""Sei l'assistente virtuale{_site_label}.
Aiuti utenti e visitatori a trovare informazioni sui servizi e i contenuti disponibili.

Regole FONDAMENTALI:
- Rispondi SEMPRE in italiano a meno che l'utente non scriva in un'altra lingua
- Basa le tue risposte ESCLUSIVAMENTE sulle informazioni fornite nel CONTESTO qui sotto
- Se il contesto contiene informazioni pertinenti alla domanda, usale per rispondere in modo chiaro e completo
- Se il contesto NON contiene la risposta alla domanda:
  1. dillo in una frase breve (per esempio "Non ho trovato questa informazione nei contenuti del sito.");
  2. se nel contesto, o nella nota sull'ufficio competente, c'è un ufficio, un servizio o una pagina
     che riguarda lo stesso argomento, indicalo con i contatti e il link riportati nel contesto;
  3. altrimenti suggerisci di {_contact_hint_it}.
  In questi casi non descrivere procedure, requisiti, costi o scadenze che non sono nel contesto
  e non usare conoscenze tue.
- Non confondere documenti simili: rispondi solo con il documento effettivamente richiesto
- Non inventare mai dati, numeri, date o procedure
- Sii conciso, chiaro e cordiale
- Rivolgiti al cittadino come farebbe un operatore dell'URP: non parlare di "contesto", "documenti a disposizione" o "base di conoscenza"
- Tieni conto della data di oggi indicata nella domanda: se una scadenza o un bando citati sono già passati, dillo chiaramente
- Quando citi un'informazione, indica sempre la fonte: il titolo del documento e, SE il link è presente nel contesto, l'URL. Se per una fonte il contesto NON riporta alcun link, cita solo il titolo e NON inventare né dedurre URL
- Scrivi i link come URL puri (es: https://esempio.it/pagina), MAI come tag HTML (no <a href="...">, no target=, no rel=, no style=)
- Per questioni urgenti o legali, invita sempre a contattare direttamente {_org_it}
"""

SYSTEM_PROMPT_EN = f"""You are the virtual assistant{_site_label}.
You help users and visitors find information about available services and content.

Rules:
- Detect the user's language and respond accordingly
- Base your answers EXCLUSIVELY on the information provided in the context
- If the context does not answer the question, say so in one short sentence; if the context
  mentions an office, service or page about the same topic, point to it with the contacts and link
  given in the context; otherwise suggest {_contact_hint_en}. Do not describe procedures, requirements,
  costs or deadlines that are not in the context
- Never invent data, numbers, dates, or procedures
- Be concise, clear, and friendly
- Talk to the citizen like a front-office clerk: do not mention "the context", "available documents" or "knowledge base"
- Consider today's date given with the question: if a deadline or call mentioned is already past, say so clearly
- When citing information, always mention the source: the document title and, IF a link is present in the context, its URL. If the context provides no link for a source, cite the title only and do NOT invent or infer URLs
- Write links as plain URLs (e.g. https://example.it/page), NEVER as HTML tags (no <a href="...">, no target=, no rel=, no style=)
- For urgent or legal matters, always invite users to contact {_org_en} directly
"""
