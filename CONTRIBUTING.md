# Contributing to Zautte

Thank you for your interest in contributing!

## How to contribute

### Bug reports

Open an issue using the **Bug report** template. Include:
- What you expected vs. what happened
- Steps to reproduce
- Environment (OS, Python version, Ollama version)
- Relevant log output

### Feature requests

Open an issue using the **Feature request** template. Describe the use case clearly — what problem does it solve and for whom?

### Pull requests

1. Fork the repository and create a branch from `main`
2. Make your changes — keep them focused on a single concern
3. Test locally (`venv/bin/python -m scripts.eval --no-llm` is a quick sanity check)
4. Open a pull request with a clear description of what changes and why

**Before submitting:**
- Ensure `.env` and any file under `data/` are not included
- Do not hardcode URLs, organization names, or credentials — everything site-specific belongs in `.env` or `config/*.json`
- Keep the widget self-contained (no external JS/CSS dependencies)

## Project structure overview

| Directory | Responsibility |
|-----------|---------------|
| `config/` | Settings + site-specific JSON overrides |
| `crawler/` | Async web crawler (httpx + BeautifulSoup) |
| `indexer/` | Chunking, embedding (Ollama), numpy vector store |
| `api/` | FastAPI backend + RAG pipeline |
| `widget/` | Self-contained JS/CSS chat widget |
| `scripts/` | Sync orchestration, eval, inbox indexer |

## Code style

- Python 3.11+, no external type stubs required
- Prefer simple, readable code over abstractions
- No comments explaining *what* the code does — only *why* when non-obvious
- All user-facing strings that may appear in logs or API responses should remain in Italian (the primary target locale) unless they are part of the English system prompt path

## License

By contributing you agree that your contributions will be licensed under the [MIT License](LICENSE).
