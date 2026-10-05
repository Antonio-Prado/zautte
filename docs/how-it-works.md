<sub>[Zautte](../README.md) › [Documentation](README.md) › How it works</sub>

# How it works

What happens to a question, and how the site's pages and PDFs become searchable chunks.

## RAG Pipeline

Each question flows through this pipeline (`api/main.py`, then `answer()` in `api/rag.py`):

```
User question
     │
     ▼
1. redact()               — masks tax codes, IBANs, cards, emails, phones in the question and in the user turns of the history (api/pii.py)
     │
     ▼
2. cache lookup           — /chat (non-streaming) without history only
     │
     ▼
3. detect_language()      — Italian vs English (keyword heuristic)
     │
     ▼
4. contextualize_query()  — follow-ups only: the LLM rewrites the question as a standalone one
     │
     ▼
5. retrieve_context()
     ├─ known facts          — curated texts from config/known_facts.json (score 1.0)
     ├─ expand_query()       — appends synonyms from config/synonyms.json
     ├─ embed_query()        — vectorizes the expanded query (Ollama bge-m3)
     ├─ hybrid_search()      — 21 candidates (3 × top-k): RRF of cosine (0.6) and BM25 (0.4)
     ├─ dedup + MIN_SIMILARITY — drops identical texts and chunks with cosine < 0.50
     ├─ rerank()             — title boost, "servizio" category boost, same-source penalty
     └─ known facts first, then chunks, cut to RETRIEVAL_TOP_K (7)
     │
     ▼
6. build_context_block()  — one header per chunk: title, type, link (no link for MIGRATED_DOMAINS)
     │
     ▼
7. build_prompt()         — system prompt (IT/EN) + last 6 messages + context + today's date + question + suggested office
     │
     ▼
8. gap log                — 0 chunks, or best cosine < RETRIEVAL_CONFIDENCE (0.52)
     │
     ▼
9. LLM                    — Ollama, Claude API or Claude on AWS Bedrock, streaming or complete
     │
     ▼
Answer + sources (deduplicated; no URL for MIGRATED_DOMAINS)
```

When streaming finds no chunk at all, the LLM is not called: the answer is a fixed "not found" message followed by the suggested office.

### Follow-up Questions

With a conversation history, `contextualize_query()` asks the LLM (`CLAUDE_REWRITE_MODEL`, `BEDROCK_REWRITE_MODEL` or `OLLAMA_MODEL`) to rewrite the question as a standalone one, so that "and what are the requirements?" is searched together with its topic. The rewritten question drives retrieval, the office suggestion and the gap log; the LLM still receives the original question and the history. If the rewrite fails, is too long, or `QUERY_REWRITE=false`, the previous user question is appended instead.

### Known Facts

`config/known_facts.json` holds curated texts for topics the site covers poorly (opening hours, procedures, links). An entry is added to the context, ahead of the retrieved chunks and with score 1.0, when the question contains one of its `keywords` and one of its `require_any` words (whole single words: multi-word keywords never match). Retrieved chunks from the same source are dropped.

### Query Expansion

`expand_query()` appends the synonyms of every `config/synonyms.json` key found in the question (plain substring match). Examples:

- `"carta identità"` → `"documento identità CIE carta d'identità elettronica"`
- `"tari"` → `"tassa rifiuti raccolta rifiuti smaltimento rifiuti tributo rifiuti"`
- `"sue"` → `"sportello edilizia permesso costruire concessione"`

### Re-ranking

`rerank()` reorders chunks without using an additional model:

- **+0.02 for each query term** present in the chunk's title
- **+0.01** if the category is `"servizio"` (service pages)
- **-0.05 × n** if the same source has already appeared (penalizes duplicates)

Each chunk keeps its cosine score, which the confidence check and the sources list use.

### Office Suggestion

`suggest_office()` matches the question against the keywords in `config/offices.json` (whole words, case-insensitive, first match wins) and returns the office with a link (`SITE_URL` + `path`, or `url`). When an office matches, the suggestion goes into the prompt: with context, as an instruction to point to that office if the context does not really answer; without context, after the "nothing found" note.

### Response Cache

Complete answers from `/chat` (non-streaming) to questions without history are cached in memory, per API process, in an LRU of 200 entries with no expiry (emptied on every restart, e.g. after a sync). The key is the MD5 of the masked question, lowercased and stripped. `/chat/stream`, used by the widget, is never cached.

### Gap Log

Questions with 0 chunks, and *weak* retrievals where even the best chunk is below `RETRIEVAL_CONFIDENCE` (0.52), are appended to `data/gaps.jsonl` as `{ts, query, chunks, weak}`. `query` is the rewritten question after personal-data masking, truncated to 200 characters, with no user. Admin endpoint: `GET /gaps`. Entries expire after `RETENTION_GAPS_DAYS`.

---

## Crawler

`crawler/crawler.py` — async crawler based on httpx and BeautifulSoup. Requests are sequential, with a `CRAWL_DELAY_SECONDS` pause after each processed response (none after HTTP errors, skipped pages or, in incremental mode, unchanged ones), a 30-second timeout and redirects followed; cookies are cleared before every request (a WordPress view-counter cookie grew until Apache answered 400 to every request).

### How It Works

1. **BFS** (breadth-first) starting from `SITE_URL` and the `extra_start_urls` in `config/crawl_extra.json`
2. Follows only links to domains in `CRAWL_ALLOWED_DOMAINS`
3. Skips URLs containing any of the `CRAWL_EXCLUDE_PATTERNS` (plus `exclude_patterns` from `crawl_extra.json`), deeper than `CRAWL_MAX_PATH_DEPTH` (10 segments; per-domain limits in `domain_max_path_depth`), or repeating a non-numeric path segment (CMS breadcrumb loops)
4. For each HTML page (responses with HTTP status ≥ 400 are skipped):
   - Extracts text with `clean_text()` (removes nav, footer, widgets, noise lines)
   - Extracts the title with `extract_title()`
   - Extracts metadata with `extract_metadata()` (category, section, date, service status)
   - Saves a `.json` in `data/crawl_cache/pages/` (pages with less than 100 characters of text are skipped)
5. Downloads PDFs (links ending in `.pdf` on allowed domains) into `data/documents/`, keeping only HTTP 200 responses that start with `%PDF`; known PDFs whose file is missing or invalid are dropped from the base index so that they are fetched again (in incremental mode a PDF whose hash is unchanged is skipped and stays out of `index.json` until the next full sync)
6. Removes from state and index every known URL not reached in this run
7. Updates state in `data/crawl_cache/crawl_state.json`
8. Saves the index in `data/crawl_cache/index.json`
9. Saves in `data/crawl_cache/pdf_link_texts.json` the text of the links to each PDF seen in this run (the most descriptive one, without "Download", "PDF" or file sizes); PDFs not linked in this run keep their previous text. Links on unchanged pages count too

### Incremental Mode

```sh
python -m crawler.crawler --incremental
```

In incremental mode the crawler:
- Loads the existing index as a base
- Still downloads every page (to discover its links), but compares the MD5 hash of the decoded HTML text (raw bytes for PDFs) with the stored one and skips text extraction and saving when it is unchanged
- Marks the changed URLs, so that `scripts.sync incremental` re-indexes only those

State is managed by the `CrawlState` class in `crawler/state.py`.

### Metadata Extraction

`extract_metadata(html, url)` returns:

| Field            | How it is determined                                     |
|------------------|----------------------------------------------------------|
| `category`       | URL path: `/services/`, `/service/` → `servizio`; `/news/`, `/news-category/`, `/notizie/` → `notizia`; `/faq` → `faq`; `/documents/`, `/public_documents/` → `documento`; `/topics/` → `argomento`; otherwise `pagina` |
| `section`        | Host name: `amministrazionetrasparente` → `trasparenza`; `sportellounico`/`suap` → `suap`; `ambitosociale` → `ambito_sociale`; `servizi.` → `servizi_online`; otherwise `sito_principale` |
| `date`           | First of the meta tags `article:modified_time`, `article:published_time`, `date`, `DC.date`, `last-modified`, else the first `<time datetime>`; truncated to `YYYY-MM-DD` |
| `service_status` | `servizio` pages only: "servizio attivo" → `attivo`; "servizio non attivo"/"servizio sospeso" → `non attivo` |

### Text Cleaning

`clean_text()` takes the text from `<main>` (or the first element whose id contains content/main/body, then whose class contains content/main/article, then `<article>`, then `<body>`) and removes:
- Tags `<script>`, `<style>`, `<noscript>`, `<nav>`, `<header>`, `<footer>`, `<aside>`, `<form>`, `<iframe>`
- CSS elements with classes `feedback`, `rating`, `survey`, `cookie`, `breadcrumb`, `pagination`, etc.
- Noise lines: "go to page", "read more", "share", "print", page numbers, etc.
- Lines shorter than 4 characters

The result has one line per text block, without blank lines.

---

## Indexer

### Semantic Chunking (`indexer/chunker.py`)

The chunking strategy works in two levels:

1. **Split by paragraph** (blank lines). Crawled pages and PDFs come out of extraction with single newlines only, so in practice this applies to inbox TXT/DOCX files: a web page or a PDF is a single block
2. **If the block exceeds 1200 characters** (`MAX_PARAGRAPH_CHARS`): further split with `RecursiveCharacterTextSplitter` (150-character overlap; separators `. `, `, `, space)

For each chunk:
- The **page title** is prepended (improves semantic retrieval)
- Noise lines are removed (navigation, "access the service", "with SPID", etc.)
- Chunks shorter than **80 characters** are discarded

Each chunk's metadata includes `source` (URL), `title`, `doc_type` (`html`, `pdf`, or `document` for inbox TXT/DOCX), `chunk_index`, `chunk_total`; HTML chunks add `category`, `section`, `date`, `service_status`; crawled PDF chunks add `pdf_pages`; inbox chunks add `category`, `filename` and `origin: "inbox"`.

The `title` of a crawled PDF is its internal `/Title`, or the cached file name (URL path plus a hash, such as `Engine_RAServeFile.php_f__Allegato_B_2022.pdf_bca21d565d62`) when there is none. Many `/Title` values say nothing (the organization's name, "Layout 1", "Microsoft Word - …"), so search results also carry a `display_title`, chosen at query time by `indexer/titles.py`: the `/Title` when it is informative and not shared by 5 or more PDFs, otherwise the file name taken from the URL and cleaned up ("Rendiconto consolidato 2020"), cut at 90 characters. When the file name is generic too ("Allegato B 2022", "DD 665-25"), the title comes from the start of the document (the first chunk): the "Oggetto:" line of resolutions and decrees, or the first line after the letterhead, followed by the file name in brackets ("Piano Dettagliato degli Obiettivi P.D.O. 2022 (Allegato B 2022)"). The text of the link that points to the PDF on the site (`data/crawl_cache/pdf_link_texts.json`, written by the crawler) comes first when it has at least three words or is not shorter than the other candidates: it is the label the site's editors chose ("Delibera di Giunta n. 75 del 12/05/2016 …"). PDFs linked only from pages that are no longer crawled, such as those on `MIGRATED_DOMAINS`, have no link text. The sources and the LLM context use `display_title`; the stored title and the ranking do not change.

### Embedding (`indexer/embedder.py`)

Embeddings are generated via Ollama using the `bge-m3` model (1024 dimensions, multilingual, 8192-token context). Until October 2026 the model was `mxbai-embed-large`: on 100 questions about municipal services, over the full index (222,480 chunks), `bge-m3` put the right page among the 7 chunks passed to the LLM 80 times against 58. To switch model see [Changing embedding model](operations.md#changing-embedding-model).

- Uses Ollama's `/api/embed` endpoint with **native batches** (16 texts per call)
- If a batch is rejected, its texts are sent one at a time; after 3 consecutive rejected batches, the rest of the run goes one text at a time
- 5xx and network errors: 3 retries with exponential backoff (1, 2, 4 s plus jitter)
- A text rejected for exceeding the model's context is cut client-side to 800, then 500, then 300 characters; other rejections fall back to the legacy `/api/embeddings` endpoint
- Control characters are replaced with spaces and texts are capped at 6000 characters; if everything fails the chunk gets a zero vector flagged `needs_reembedding` (`python -m scripts.sync reembed` computes it again)
- `embed_query()` for user queries (single call)

### Main Indexer (`indexer/indexer.py`)

```sh
python -m indexer.indexer              # index sources not yet in the vector store
python -m indexer.indexer --reset      # clear and re-index
python -m indexer.indexer --stats      # show statistics
python -m indexer.indexer --only-html  # HTML pages only
python -m indexer.indexer --only-pdf   # PDFs only
```

Reads `data/crawl_cache/index.json`. For each document it compares the new chunks with the stored ones: only new or changed chunks (or chunks with a zero vector) are embedded, in groups of 50; the others only have their metadata refreshed, and chunks of the same source that no longer exist are removed. Disk writes are deferred: a checkpoint every 30 minutes and one at the end.

### Document Inbox (`scripts/inbox_indexer.py`)

Allows indexing manually uploaded documents:

```sh
# Place files in:
data/inbox/resolution.pdf
data/inbox/resolution.json   # optional metadata

# Process manually:
python -m scripts.inbox_indexer

# Or in watch mode (polling every 60s; --interval to change it):
python -m scripts.inbox_indexer --watch
```

**Supported formats**: PDF, TXT, DOCX

**Optional metadata** (`.json` file alongside the document; defaults: file name without extension as title, `file://<name>` as source, category `documento`):
```json
{
    "title": "Resolution no. 15 of 2024",
    "source_url": "https://www.myorg.com/documents/2024/15",
    "category": "resolutions"
}
```

Processed files are moved to `data/inbox/processed/` (or `data/inbox/errors/` on failure, or with less than 100 characters of text). Inbox chunks are marked `origin: "inbox"`, so the stale-source cleanup of the syncs never removes them. Every `scripts.sync` mode except `reembed` also processes the inbox at the end.

---

## Vector Store

`indexer/vector_store.py` — numpy implementation, no external database.

### Data Structure

Three files on disk, written atomically (temporary file + rename):

| File                          | Content                          |
|-------------------------------|----------------------------------|
| `data/vectorstore/embeddings.npy` | numpy matrix (N × 1024) float32 |
| `data/vectorstore/metadata.json`  | List of dicts with chunk text and metadata |
| `data/vectorstore/ids.json`       | List of IDs (MD5 hashes)         |

### Cosine Search

Vectors are normalized at insertion. Search is a simple matrix product:

```python
scores = _embeddings @ query_vector   # cosine similarity
```

### Hybrid Search (BM25 + Vector)

`hybrid_search()` combines the two rankings via **Reciprocal Rank Fusion (RRF)**:

```
final_score(doc) = 0.6 × RRF_vector(doc) + 0.4 × RRF_bm25(doc)
RRF(rank) = 1 / (60 + rank + 1)
```

Each ranking considers top_k × 20 candidates. Results are ordered by fused score, but each hit's `score` is its cosine similarity, which `MIN_SIMILARITY` and `RETRIEVAL_CONFIDENCE` use.

The BM25 index (`BM25Okapi`, lowercase letter-only tokens: numbers are not indexed) is built when the store is loaded and rebuilt before the next search whenever chunks were added or removed. It requires the `rank-bm25` package; without it the search uses the vector ranking only.

### Idempotent Upsert

`upsert_chunks()` identifies each chunk with an MD5 hash of `source_url + chunk_index + text[:64]`. If the ID exists, vector and metadata are replaced in place; otherwise the chunk is appended. A chunk passed without a vector (unchanged text) only has its metadata refreshed; a zero vector is stored but flagged `needs_reembedding`. This makes the operation safe to run multiple times.

### Operational Note

The vector store is loaded into memory once per API process (at startup, or at the first query if Ollama was unreachable); `start.sh` runs two uvicorn processes (IPv4 and IPv6), each with its own copy. Chunks added by a standalone `indexer.indexer` or `inbox_indexer` run are not visible until the API restarts; `scripts.sync` restarts it at the end of every mode.

---

← [Configuration](configuration.md) · [Documentation index](README.md) · [Backend API](api.md) →
