"""
Centralized configuration for Zautte.
Edit this file to adapt the system to your environment.
"""

import datetime as _datetime
import logging
import os
from pathlib import Path

# Names a .env file must never set: they steer the dynamic loader, the interpreter or the
# lookup of programs, and the sync jobs (root) start other programs.
_FORBIDDEN_ENV_PREFIX = ("LD_", "DYLD_", "PYTHON", "SSL_CERT", "REQUESTS_CA", "CURL_CA", "BASH_", "MALLOC_")
_FORBIDDEN_ENV_NAMES = {"PATH", "IFS", "ENV", "SHELL", "HOME", "USER", "LOGNAME", "TMPDIR"}

try:
    from dotenv import dotenv_values
    # ZAUTTE_ENV_FILE: the unprivileged sync account reads a copy of .env without the secrets
    # it does not need (see scripts/setup_sync_user.sh)
    _env_file = os.environ.get("ZAUTTE_ENV_FILE") or Path(__file__).parent.parent / ".env"
    for _k, _v in dotenv_values(_env_file).items():
        if (_v is not None and not _k.upper().startswith(_FORBIDDEN_ENV_PREFIX)
                and _k.upper() not in _FORBIDDEN_ENV_NAMES and _k not in os.environ):
            os.environ[_k] = _v
except ImportError:
    # python-dotenv is optional: without it, only environment variables apply
    pass


def now_local() -> _datetime.datetime:
    """Current server time with local timezone (timezone-aware).
    Used for all timestamps recorded in the data files."""
    return _datetime.datetime.now(_datetime.UTC).astimezone()


# --- Paths ---
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
# Where a sync job that is not root leaves its requests to the watchdog (reload / restart the API)
SYNC_CONTROL_DIR = Path(os.getenv("SYNC_CONTROL_DIR", "/var/db/zautte-sync"))
VECTOR_DB_DIR = DATA_DIR / "vectordb"
DOCUMENTS_DIR = DATA_DIR / "documents"
CRAWL_CACHE_DIR = DATA_DIR / "crawl_cache"

# --- Organization customizations ---
# Folder (outside the repository) with the installation-specific files: widget
# pages and images (widget/), restricted area (admin_panel/) and config files
# (config/). Files here replace the same-named ones in the repository; those
# that are missing stay the generic files. Empty = repository files only.
INSTANCE_DIR = os.getenv("INSTANCE_DIR", "").strip()

# --- Target site ---
# Required: set in .env
SITE_URL  = os.getenv("SITE_URL", "")
SITE_NAME = os.getenv("SITE_NAME", "")

# --- Crawling ---
CRAWL_MAX_PAGES = 0            # 0 = no limit
CRAWL_DELAY_SECONDS = 0.3      # pause between requests (~3 pages/second)
# Allowed domains: comma-separated list in .env
# Example: CRAWL_ALLOWED_DOMAINS=www.comune.example.it,trasparenza.comune.example.it
CRAWL_ALLOWED_DOMAINS = [
    d.strip()
    for d in os.getenv("CRAWL_ALLOWED_DOMAINS", "").split(",")
    if d.strip()
]
# Never fetch a host that resolves to a loopback, private or otherwise non-public address
CRAWL_BLOCK_PRIVATE_IPS = os.getenv("CRAWL_BLOCK_PRIVATE_IPS", "true").strip().lower() in ("1", "true", "yes", "on")
# Allowed hosts that legitimately resolve to a private address (split-horizon DNS: the server
# sees an internal address for one of its own sites). Comma-separated EXACT host names:
# the subdomains of a name listed here are not exempted.
CRAWL_PRIVATE_OK_HOSTS = [
    d.strip().lower()
    for d in os.getenv("CRAWL_PRIVATE_OK_HOSTS", "").split(",")
    if d.strip()
]
# Size limits of a single download, in MB (pages and feeds / PDFs and other attachments)
CRAWL_MAX_PAGE_MB = int(os.getenv("CRAWL_MAX_PAGE_MB", "20"))
CRAWL_MAX_FILE_MB = int(os.getenv("CRAWL_MAX_FILE_MB", "100"))
CRAWL_EXCLUDE_PATTERNS = [
    "/wp-admin/", "/feed/", "/tag/", "?replytocom=",
    ".jpg", ".png", ".gif", ".ico", ".css", ".js",
    "?page=",      # news pagination pages
    "/news?",      # news with query parameters
    "/login/",     # login area
]

# Maximum URL path depth (segments separated by /) — global limit.
CRAWL_MAX_PATH_DEPTH = 10

# Depth limits for specific domains (override the global limit).
# Format: {"dominio.example.it": 5}
# Useful when a CMS generates very deep URLs with duplicate content.
CRAWL_DOMAIN_MAX_PATH_DEPTH: dict[str, int] = {}
CRAWL_EXTRA_START_URLS: list[str] = []
# Different hosts serving the same files (e.g. CMS attachments also on a CDN/bucket):
# [{"path_prefix": "/s3/…/", "hosts": [...], "canonical_host": "..."}]. The crawler
# rewrites these URLs onto the canonical host, so a file is downloaded and indexed once.
CRAWL_URL_ALIASES: list[dict] = []
# "Section" metadata label derived from the host name, in order, the first rule wins:
# [{"host_contains": "suap", "section": "suap"}]. With no rules SECTION_DEFAULT applies.
CRAWL_SECTION_RULES: list[dict] = []
SECTION_DEFAULT = "sito_principale"
# URL fragments of PDFs not to download (PDFs do not go through CRAWL_EXCLUDE_PATTERNS,
# which contains ".pdf" to keep them out of page navigation)
CRAWL_PDF_EXCLUDE_PATTERNS: list[str] = []
# Near-real-time news sources (scripts/news_sync.py): "news" key of
# crawl_extra.json → {"wordpress_api": ".../wp-json/wp/v2", "rss": [...],
# "attachment_hosts": [...], "section": "..."}
NEWS_SOURCES: dict = {}

# Site-specific overrides — loaded from config/crawl_extra.json if present.
# Allows adding exclusion patterns and depth limits without changing the code.
try:
    import json as _json_settings
    _crawl_extra_path = Path(__file__).parent / "crawl_extra.json"
    if _crawl_extra_path.exists():
        _crawl_extra = _json_settings.loads(_crawl_extra_path.read_text(encoding="utf-8"))
        CRAWL_EXCLUDE_PATTERNS += _crawl_extra.get("exclude_patterns", [])
        CRAWL_DOMAIN_MAX_PATH_DEPTH.update(_crawl_extra.get("domain_max_path_depth", {}))
        CRAWL_EXTRA_START_URLS += _crawl_extra.get("extra_start_urls", [])
        CRAWL_URL_ALIASES += _crawl_extra.get("url_aliases", [])
        CRAWL_SECTION_RULES += _crawl_extra.get("section_rules", [])
        CRAWL_PDF_EXCLUDE_PATTERNS += _crawl_extra.get("pdf_exclude_patterns", [])
        NEWS_SOURCES.update(_crawl_extra.get("news", {}))
except (OSError, ValueError, TypeError, AttributeError) as _e:
    logging.getLogger(__name__).warning("crawl_extra.json non caricato: %s", _e)

# --- Migrated / retired domains ---
# Domains whose resources are no longer reachable at the old address because
# the portal was replaced by a new platform that redirects everything to
# a generic landing page (e.g. a transparency portal replaced by a new vendor).
# Chunks already indexed from these domains STAY in the vector store — the text is
# still valid and useful — but:
#   - their source URL is NOT shown to the user nor passed to the LLM: it would be
#     a dead link. The document is cited by title/section only.
#   - the full sync does NOT treat them as "stale": they must not be removed just
#     because they can no longer be crawled (otherwise much of the knowledge base would be lost).
# The original URLs stay in the metadata: if the vendor later provides a stable
# redirect map, they can be remapped. Comma-separated list in
# .env: MIGRATED_DOMAINS=vecchio.example.it,nuovo.example.it/percorso-dismesso (a domain or
# a path prefix). Empty = no migrated domains.
MIGRATED_DOMAINS = [
    d.strip()
    for d in os.getenv("MIGRATED_DOMAINS", "").split(",")
    if d.strip()
]


def is_migrated_source(url: str) -> bool:
    """True if the URL belongs to a migrated/retired domain (link no longer valid)."""
    return bool(url) and any(d in url for d in MIGRATED_DOMAINS)


# --- Chunking ---
CHUNK_SIZE = 800               # characters per chunk
CHUNK_OVERLAP = 100            # overlap between consecutive chunks

# --- Embedding ---
# Generated by Ollama (same service as the LLM, no additional dependency).
# bge-m3: 1024 dim, multilingual, 8192-token context. In the 03/10/2026 test
# on 100 questions the right page ended up among the 7 passages given to the model 75 times
# versus 62 for mxbai-embed-large (the previous model, trained on English).
# Changing the model requires recomputing all vectors: scripts/reembed.py.
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "bge-m3")
EMBEDDING_DIMENSION = 1024

# --- Vector Store ---
# numpy implementation (pure Python, no compilation required)
VECTOR_STORE_DIR = DATA_DIR / "vectorstore"
RETRIEVAL_TOP_K = 7            # chunks to retrieve for each query

# --- LLM ---
# Choose: "ollama" (local, full privacy), "claude" (direct Anthropic API)
# or "bedrock" (Claude models on AWS Bedrock, processing in the EU)
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama")

# Ollama (local)
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")

# Claude API (requires a DPA with Anthropic for use in public administration)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
# Reasoning effort for models that reason by default (Sonnet 5.x and
# later): "low" keeps latency and costs down, as an informational assistant needs.
LLM_EFFORT = os.getenv("LLM_EFFORT", "low")

# Question rewriting for retrieval in turns after the first:
# retrieval uses only the text of the current question, so a follow-up
# like "quali sono i requisiti?" loses the topic of the previous turn.
# With QUERY_REWRITE on, the model makes the question self-contained before
# the search (only when there is conversation history). The model for the
# rewrite is by default the same as for the answer; a cheaper one can be
# set in .env (e.g. claude-haiku-4-5).
QUERY_REWRITE = os.getenv("QUERY_REWRITE", "true").strip().lower() in ("1", "true", "yes", "on")
CLAUDE_REWRITE_MODEL = os.getenv("CLAUDE_REWRITE_MODEL", CLAUDE_MODEL)

# Claude on AWS Bedrock (LLM_PROVIDER=bedrock; requires `pip install "anthropic[bedrock]"`).
# The "eu." inference profile processes requests only in EU AWS regions;
# calling from Milan (eu-south-1) the destinations are Milan, Frankfurt,
# Paris, Ireland, Spain and Stockholm. Credentials from the standard AWS chain
# (AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY, AWS_PROFILE or AWS_BEARER_TOKEN_BEDROCK).
BEDROCK_AWS_REGION = os.getenv("BEDROCK_AWS_REGION", "eu-south-1")
BEDROCK_MODEL = os.getenv("BEDROCK_MODEL", "eu.anthropic.claude-sonnet-4-6")
BEDROCK_REWRITE_MODEL = os.getenv("BEDROCK_REWRITE_MODEL", BEDROCK_MODEL)

# --- Personal data in questions (see api/pii.py) ---
# With PII_REDACTION on, tax codes, IBANs, payment cards, emails and
# phone numbers are replaced by a placeholder before being sent to the model and
# before being written to the logs. PII_KEEP_EMAIL_DOMAINS: institutional domains whose
# emails stay in clear (e.g. "comune.example.it,pec.example.it").
PII_REDACTION = os.getenv("PII_REDACTION", "true").strip().lower() in ("1", "true", "yes", "on")
PII_KEEP_EMAIL_DOMAINS = [
    d.strip().lower().lstrip("@") for d in os.getenv("PII_KEEP_EMAIL_DOMAINS", "").split(",") if d.strip()
]
# With PII_BLOCK on, a question that contains personal data (those
# recognized by PII_REDACTION or a first-person introduction, such as
# «mi chiamo ...») is NOT processed: no search, no model, no
# logging of the text, only a fixed answer inviting the user to rephrase.
PII_BLOCK = os.getenv("PII_BLOCK", "true").strip().lower() in ("1", "true", "yes", "on")

# --- Log retention (days; 0 = no expiry) ---
# Applied by scripts/purge_logs.py (daily cron). Values to be agreed with
# the DPO and reported in the privacy notice.
#   RETENTION_USAGE_TEXT_DAYS: question text in usage.jsonl (afterwards only
#                              date, user and metrics remain) and answers in
#                              answers.jsonl
#   RETENTION_USAGE_DAYS:      usage.jsonl entries
#   RETENTION_GAPS_DAYS:       unanswered questions in gaps.jsonl
#   RETENTION_FEEDBACK_DAYS:   feedback (vote, question, comment, author)
RETENTION_USAGE_TEXT_DAYS = int(os.getenv("RETENTION_USAGE_TEXT_DAYS", "90"))
RETENTION_USAGE_DAYS = int(os.getenv("RETENTION_USAGE_DAYS", "365"))
RETENTION_GAPS_DAYS = int(os.getenv("RETENTION_GAPS_DAYS", "180"))
RETENTION_FEEDBACK_DAYS = int(os.getenv("RETENTION_FEEDBACK_DAYS", "365"))
# RETENTION_STATS_DAYS: most frequent questions and per-user token records in stats.json
# (applied by the API itself every time the statistics are saved)
RETENTION_STATS_DAYS = int(os.getenv("RETENTION_STATS_DAYS", "90"))

# --- API Backend ---
# Comma-separated list of addresses the API listens on (api/serve.py). Local only by default.
API_HOST = os.getenv("API_HOST", "127.0.0.1,::1")
API_PORT = int(os.getenv("API_PORT", "8000"))
API_CORS_ORIGINS = os.getenv("API_CORS_ORIGINS", "http://localhost:8000").split(",")
# Proxy or load balancer in front of the API: comma-separated addresses or networks
# from which X-Forwarded-For and X-Forwarded-Proto are accepted, so per-IP limits and the
# log see the real client and not the proxy. Never "*": port 8000 also answers
# on the internal network and anyone could write the header themselves.
FORWARDED_ALLOW_IPS = os.getenv("FORWARDED_ALLOW_IPS", "127.0.0.1,::1")
# API_DOCS=true publishes the interactive documentation (/docs and /openapi.json):
# useful in development, off by default because in production it would show
# anyone the complete list of endpoints and parameters.
API_DOCS = os.getenv("API_DOCS", "false").strip().lower() in ("1", "true", "yes", "on")

# Largest request body the API reads (bytes); anything bigger gets 413
MAX_BODY_BYTES = int(os.getenv("MAX_BODY_BYTES", "131072"))

# --- Limits per account (api/account_guard.py) ---
LOGIN_MAX_FAILURES = int(os.getenv("LOGIN_MAX_FAILURES", "10"))     # wrong passwords before the account refuses logins
LOGIN_LOCK_SECONDS = int(os.getenv("LOGIN_LOCK_SECONDS", "900"))    # window of the count and duration of the refusal
RESET_EMAILS_PER_HOUR = int(os.getenv("RESET_EMAILS_PER_HOUR", "3"))

# --- Answers at once (api/llm_gate.py) ---
LLM_MAX_CONCURRENT = int(os.getenv("LLM_MAX_CONCURRENT", "8"))   # in the whole process
LLM_MAX_PER_USER = int(os.getenv("LLM_MAX_PER_USER", "2"))       # for each user

# --- Admin endpoint authentication ---
# Set in .env to protect /gaps and /stats
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")
# With an empty ADMIN_API_KEY every admin request is refused, unless this is true
# (development ONLY: it opens all admin endpoints and the detailed /health to anyone)
ADMIN_AUTH_DISABLED = os.getenv("ADMIN_AUTH_DISABLED", "false").lower() == "true"
# Wrong attempts allowed per address: once the maximum is reached the address is
# blocked (429) for ADMIN_LOCKOUT_SECONDS, even with the right key.
ADMIN_MAX_ATTEMPTS = int(os.getenv("ADMIN_MAX_ATTEMPTS", "5"))
ADMIN_LOCKOUT_SECONDS = int(os.getenv("ADMIN_LOCKOUT_SECONDS", "900"))
# Address of the restricted area (e.g. /nome). Only in .env: empty = area disabled.
ADMIN_PATH = ("/" + os.getenv("ADMIN_PATH", "").strip().strip("/")) if os.getenv("ADMIN_PATH", "").strip().strip("/") else ""

# --- User authentication (email+password login for a restricted-group pilot) ---
# AUTH_ENABLED=true enables the gate on /chat, /chat/stream, /feedback: whoever has not
# logged in cannot use the bot. It serves to understand who uses it,
# when and how much (see data/usage.jsonl and GET /usage/summary).
# AUTH_SECRET: secret used to sign session tokens (REQUIRED if enabled).
#   Generate with:  python -c "import secrets; print(secrets.token_hex(32))"
# Users are created with:  python -m scripts.adduser --email ... --name "..."
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")
AUTH_SECRET = os.getenv("AUTH_SECRET", "")
AUTH_TOKEN_TTL_DAYS = int(os.getenv("AUTH_TOKEN_TTL_DAYS", "7"))
# users.json lives in data/ (already in .gitignore): credentials kept out of version control
USERS_FILE = DATA_DIR / "users.json"
# Questions per day per user on /chat and /chat/stream (0 = no limit).
# Server calendar day: at midnight the count restarts from zero. With
# AUTH_ENABLED=false the count is per IP address. See api/quota.py.
DAILY_QUESTION_LIMIT = int(os.getenv("DAILY_QUESTION_LIMIT", "20"))

# --- Email sending (SMTP) — to deliver credentials to participants ---
# Organization's internal relay (recipients in the organization's domain):
#   SMTP_HOST=mail.example.it  SMTP_PORT=25  (no auth or TLS)
# SMTP_USER/PASSWORD and SMTP_STARTTLS are needed only if the relay requires them.
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "25"))
SMTP_FROM = os.getenv("SMTP_FROM", "")
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_STARTTLS = os.getenv("SMTP_STARTTLS", "false").strip().lower() in ("1", "true", "yes", "on")
# SMTP_SSL=true: implicit TLS from the start of the connection (port 465, e.g. Carbonio);
# alternative to SMTP_STARTTLS (port 587).
SMTP_SSL = os.getenv("SMTP_SSL", "false").strip().lower() in ("1", "true", "yes", "on")
# URL of the page where participants log in (inserted in the email)
PILOT_LOGIN_URL = os.getenv("PILOT_LOGIN_URL", "")
# Public address of the site (https://bot.example.it): used for the links in emails
# (password reset). Empty = the origin of PILOT_LOGIN_URL.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "")

# Address that receives an email for every report from the widget (👎 with
# comment and/or link). Empty = no notification; the report remains
# visible in the dashboard anyway (administrator view).
FEEDBACK_NOTIFY_EMAIL = os.getenv("FEEDBACK_NOTIFY_EMAIL", "").strip()

# --- System prompts ---
_site_label = f" di {SITE_NAME}" if SITE_NAME else ""
# Whom to contact when the information is missing ("il Comune di …" for municipal sites)
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
