<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/zautte-logo-dark.svg">
    <img src="widget/zautte-logo.svg" alt="Zautte" width="320">
  </picture>
</p>

<h3 align="center">A virtual assistant that answers questions using only your website's content</h3>

<p align="center">
  <a href="https://github.com/Antonio-Prado/zautte/actions/workflows/ci.yml"><img src="https://github.com/Antonio-Prado/zautte/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/Antonio-Prado/zautte/actions/workflows/codeql.yml"><img src="https://github.com/Antonio-Prado/zautte/actions/workflows/codeql.yml/badge.svg" alt="CodeQL"></a>
  <a href="https://github.com/Antonio-Prado/zautte/releases"><img src="https://img.shields.io/github/v/release/Antonio-Prado/zautte?color=003366" alt="Release"></a>
  <a href="https://doi.org/10.5281/zenodo.23452491"><img src="https://zenodo.org/badge/DOI/10.5281/zenodo.23452491.svg" alt="DOI"></a>
  <img src="https://img.shields.io/badge/python-3.11-3776AB?logo=python&logoColor=white" alt="Python 3.11">
  <img src="https://img.shields.io/badge/platform-FreeBSD-AB2B28?logo=freebsd&logoColor=white" alt="FreeBSD">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT license"></a>
</p>

Zautte is a **RAG** (Retrieval-Augmented Generation) assistant for public-sector and other content-heavy websites. It crawls the site's pages and PDFs, finds the passages relevant to each question and lets an LLM answer **from those passages only, citing its sources**. Personal data in questions is masked before it reaches the model or the logs.

It is built to run on a single FreeBSD server with no database and no containers. What belongs to one organisation (pages, logos, known facts, changelog) is kept outside this repository, see [Configuration](docs/configuration.md).

**Why "Zautte"?** In the dialect of San Benedetto del Tronto, a *zautte* is a sea laborer, a dockworker. *Li zautte* are young, strong workers who, historically coordinated by a foreman known as *lu sbarzòcche*, unload the fish from the boats just back in port and carry it to the fish market.

<p align="center">
  <img src="docs/images/zautte-chat-desktop.png" alt="Zautte answering a question about changing residence, desktop view" width="66%">
  &nbsp;
  <img src="docs/images/zautte-chat-phone.png" alt="Zautte answering a question about the identity card, phone view" width="22.5%">
</p>
<p align="center"><sub>Screenshots with invented data; the widget speaks Italian and English.</sub></p>

## Features

<table>
<tr>
<td width="50%">🔎 <b>Answers grounded in the site</b><br>Hybrid search (semantic + BM25) over pages, PDFs and uploaded documents; every answer lists its sources.</td>
<td width="50%">💬 <b>Follow-up questions</b><br>"And what does it cost?" is rewritten with its topic before searching, so conversations keep their context.</td>
</tr>
<tr>
<td width="50%">🛡️ <b>Privacy by design</b><br>Tax codes, IBANs, cards, emails and phone numbers are masked; embeddings are always computed locally; retention periods for logs.</td>
<td width="50%">🔌 <b>Drop-in widget</b><br>One <code>&lt;script&gt;</code> tag, vanilla JS with no dependencies, streaming answers, floating or inline, mobile-friendly, Italian and English.</td>
</tr>
<tr>
<td width="50%">👍 <b>Feedback loop</b><br>Users rate answers and can point to the right page; admins resolve reports and the user is notified by email.</td>
<td width="50%">🔄 <b>Self-updating index</b><br>News are indexed every 15 minutes; weekly incremental and monthly full crawl; only new or changed chunks are re-embedded; nightly backups and a watchdog.</td>
</tr>
<tr>
<td width="50%">🧠 <b>Your choice of LLM</b><br>Claude through the Anthropic API or AWS Bedrock (EU, Milan region), or a fully local model with Ollama.</td>
<td width="50%">📊 <b>Restricted admin area</b><br>Service status, indexed content, activity, feedback, users, token costs and crawl history, on an address of your choice (<code>ADMIN_PATH</code>) behind a key, with a lockout after repeated wrong keys.</td>
</tr>
</table>

## How it works

```mermaid
sequenceDiagram
    autonumber
    actor U as Colleague / citizen
    participant W as Widget
    participant A as API
    participant S as Vector store<br/>(numpy + BM25)
    participant L as LLM
    participant I as Indexer<br/>(crawler, news, inbox)

    loop every 15 min, weekly, monthly
        I->>S: crawl site, news sources, feed, PDF/DOCX<br/>chunk + embed + index
        I->>A: reload index (no restart)
    end

    Note over U,A: sign-in when login is enabled (AUTH_ENABLED)
    U->>W: sign in (email + password)
    W->>A: POST /auth/login
    A-->>W: signed token

    U->>W: question
    W->>A: POST /chat/stream (token)
    alt the question contains personal data
        A-->>W: fixed reply: rephrase it<br/>(nothing sent to the LLM, nothing logged)
    else daily limit of questions reached
        A-->>W: 429, the widget explains it
    else
        A->>A: mask residual personal data
        alt greeting, thanks, insult
            A-->>W: fixed reply (no search, no LLM)
        else
            A->>A: rewrite follow-ups, expand synonyms
            A->>S: hybrid search (semantic + BM25)
            S-->>A: best chunks
            A->>L: question + chunks + known facts<br/>("use nothing else")
            L-->>A: answer with sources
            A-->>W: answer, sources (streamed)
            opt site cannot answer
                A->>A: log question for review
            end
        end
    end
    W-->>U: reply
```

New items from the configured news sources (for example a WordPress news site or an RSS feed) are indexed within 15 minutes, with their linked pages and attachments, and the API reloads the index without restarting. A question that contains personal data is not processed at all and the user is asked to rephrase it; any other question is masked, rewritten if it is a follow-up, expanded with synonyms and searched both by meaning and by keywords. The best chunks, plus curated "known facts" for topics the site covers poorly, go to the LLM with instructions to use nothing else. Questions the site cannot answer are logged for review. Details: [How it works](docs/how-it-works.md).

## Reference figures

<table>
  <tr>
    <td align="center"><b>270,745</b><br><sub>chunks indexed</sub></td>
    <td align="center"><b>13,939</b><br><sub>pages and documents</sub></td>
    <td align="center"><b>80 / 100</b><br><sub>test questions with the right page<br>among the chunks given to the LLM</sub></td>
    <td align="center"><b>15 min</b><br><sub>to index new news items;<br>incremental crawl weekly,<br>full crawl monthly</sub></td>
  </tr>
</table>

<sub>Figures from a reference installation. The retrieval score went from 58 to 80 out of 100 when the embedding model changed from `mxbai-embed-large` to `bge-m3`.</sub>

<details>
<summary><b>Restricted admin area</b> (invented data)</summary>
<br>
<p align="center"><img src="docs/images/zautte-admin-area.png" alt="Admin dashboard: service status, indexed content, activity and feedback" width="80%"></p>
</details>

## Quick start

You need FreeBSD 15 with [Ollama](https://ollama.com) installed and running; the full list of prerequisites is in [Installation](docs/installation.md).

```sh
git clone https://github.com/Antonio-Prado/zautte.git /opt/chatbot
cd /opt/chatbot
sh scripts/setup_freebsd.sh            # as root: packages, virtualenv, requirements
ollama pull bge-m3                     # embedding model, always local

cp .env.example .env                   # set SITE_URL, SITE_NAME, LLM_PROVIDER, ADMIN_API_KEY, ADMIN_PATH, keys
venv/bin/python -m scripts.sync full   # crawl the site and build the index
venv/bin/python -m uvicorn api.main:app --host 0.0.0.0 --port 8000
```

Then add the widget to your site's pages:

```html
<script>
  window.ChatbotConfig = { apiUrl: 'https://chatbot.example.org', title: 'Zautte' };
</script>
<script src="https://chatbot.example.org/widget/chatbot-widget.js" defer></script>
```

> [!NOTE]
> With `LLM_PROVIDER=ollama` nothing leaves the server. With `claude` or `bedrock`, the masked question, the last turns of the conversation and the retrieved passages are sent to the provider. See [Privacy and Security](docs/privacy-and-security.md).

## Documentation

| | |
|---|---|
| 🏗️ [Architecture](docs/architecture.md) | Components, technology stack, repository layout |
| 📦 [Installation](docs/installation.md) | Prerequisites, setup on FreeBSD, first indexing |
| ⚙️ [Configuration](docs/configuration.md) | `.env` variables, retrieval thresholds and weights |
| 🧭 [How it works](docs/how-it-works.md) | RAG pipeline, crawler, indexer, vector store |
| 🔌 [Backend API](docs/api.md) | Endpoints, streaming, login, admin authentication |
| 💬 [Frontend Widget](docs/widget.md) | Embedding the chat, options, bundled pages |
| 🛠️ [Operations](docs/operations.md) | Cron jobs, rc.d service, monitoring, troubleshooting, changing models |
| 🛡️ [Privacy and Security](docs/privacy-and-security.md) | GDPR, what is stored, rate limits |
| 🚀 [Releasing](docs/releasing.md) | Versions, [changelog](CHANGELOG.md), tags and releases |

## Built with

Python 3.11 · FastAPI and uvicorn · Ollama (`bge-m3` embeddings) · numpy · rank-bm25 · httpx, BeautifulSoup and lxml · pypdf · a hosted LLM (Anthropic API, AWS Bedrock) or a local one with Ollama · vanilla JavaScript · FreeBSD rc.d, cron and newsyslog.

## Contributing and security

Contributions are welcome: see [CONTRIBUTING.md](CONTRIBUTING.md) and the [Code of Conduct](CODE_OF_CONDUCT.md). To report a vulnerability, follow [SECURITY.md](SECURITY.md); for accessibility, see [ACCESSIBILITY.md](ACCESSIBILITY.md).

Released under the [MIT License](LICENSE) · © 2026 Antonio Prado.

<sub>Documentation updated: October 2026</sub>
