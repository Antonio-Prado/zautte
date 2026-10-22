# Contributing to Zautte

Thank you for your interest in contributing! Everyone taking part in this project is expected to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

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
3. Run `ruff check .` and `python -m pytest -q tests/unit` (as CI does) and, if you touch the widget or the bundled pages, `python tests/a11y/check.py` (see [Accessibility testing](docs/accessibility-testing.md)). For retrieval changes, `venv/bin/python -m scripts.eval --no-llm` is a quick sanity check (it needs a built index and Ollama)
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
| `admin_panel/` | Restricted admin area (HTML/JS) |
| `tests/` | `unit/`: regression tests (pytest); `a11y/`: accessibility checks (Playwright + axe-core) |
| `scripts/` | Sync orchestration, eval, inbox indexer |

## Changes and releases

- Add a line under `[Unreleased]` in [CHANGELOG.md](CHANGELOG.md) for every change to the code; maintainers turn it into a version (see [Releasing](docs/releasing.md)). CI fails if `version.json`, `CITATION.cff`, `publiccode.yml` and the newest CHANGELOG heading disagree; contributors only touch `[Unreleased]`.
- Keep what belongs to one organisation (pages, logos, known facts) out of the repository: it goes in the folder pointed to by `INSTANCE_DIR`, see [Configuration](docs/configuration.md).

## Code style

- Python 3.11+, no external type stubs required
- Prefer simple, readable code over abstractions
- No comments explaining *what* the code does — only *why* when non-obvious
- User-facing strings in logs and API responses are Italian by default (the widget also has English); keep new strings in the same language as their neighbours

## License

By contributing you agree that your contributions will be licensed under the [MIT License](LICENSE).
