# Changelog

All notable changes to Zautte are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/) (`0.x` while it is a pilot: minor versions for new
features, patch versions for fixes). The version lives in `version.json`; CI fails when it does
not match the latest entry below. Changes that only touch an installation's own files (known
facts, texts, logos, see [Configuration](docs/configuration.md)) are not released.

## [Unreleased]

## [0.4.2] - 2026-10-20

### Security
- An empty `ADMIN_API_KEY` no longer opens the admin endpoints and the detailed `/health`: every admin
  request is refused. Development setups that relied on the open mode must set `ADMIN_AUTH_DISABLED=true`.
- The public `/health` returns only `status` and `uptime_seconds`; `llm_provider` and `hybrid_search`
  moved to the admin view.

### Changed
- The documentation no longer states which model, OS release or dated changes the reference installation uses,
  and the README no longer names the municipality running it.
- The README screenshots have new file names (`zautte-chat-desktop.png`, `zautte-chat-phone.png`,
  `zautte-admin-area.png`), so that browsers and image caches show the new pictures.

## [0.4.1] - 2026-10-19

### Changed
- The AI badge in the header of the chat reads "AI" when the widget is in English ("IA" in Italian).
- The README screenshots show the generic English pages with invented data (chat on a desktop and on a
  phone, restricted area).

### Removed
- `aiofiles` and `pydantic-settings` from `requirements.txt`: nothing imports them.

## [0.4.0] - 2026-10-19

### Changed
- The generic pages (`dashboard.html`, `pilot.html`, `come-funziona.html`, `embed-snippet.html`), the
  footer, the manifest and the restricted area (login page and panel) are in English, and the widget
  on them starts in English. An installation keeps its own language through `INSTANCE_DIR`.
- The anchor of the accessibility section of `come-funziona.html` is `#accessibility`.
- Log messages, command-line help and printed messages of the services and scripts are in English
  (the text shown to users, the emails and the answers are unchanged). The restricted area reads the
  new sync log lines as well as the old Italian ones.
- The accessibility check runs the pages with the Italian widget and also checks the English pages;
  its report is in English.

## [0.3.2] - 2026-10-19

### Changed
- The Zenodo archive was deleted and is made again from this release: the old DOI is removed from
  `CITATION.cff` and from the README until the new one exists.

## [0.3.1] - 2026-10-19

### Changed
- Comments and docstrings in the code are in English (the text shown to users, log messages and the
  language of the answers are unchanged).

## [0.3.0] - 2026-10-19

### Added
- `section_rules` in `crawl_extra.json`: rules that label each page with a `section` from its host
  name (first match wins, `sito_principale` otherwise). The labels used to be fixed in the crawler.
- `config/crawl_extra.example.json` shows every key, including `news` and `section_rules`.
- `scripts/check_version.py` also checks `softwareVersion` in `publiccode.yml`.

### Changed
- Documentation brought in line with 0.2.0: restricted area and lockout (`ADMIN_PATH`,
  `ADMIN_MAX_ATTEMPTS`, `ADMIN_LOCKOUT_SECONDS`), `INSTANCE_DIR`, generic pages and footer,
  releasing; the sequence diagram in the README shows sign-in, the block on questions with
  personal data, the daily limit and the fixed replies.
- News sources are described generically (WordPress, RSS) instead of as a municipal bulletin.
- `publiccode.yml`: the repository owner is the author, version 0.3.0.
- Scripts and messages no longer name a specific organisation.
- The host hardening section of the operations guide is a list of recommendations; the state of a particular machine is no longer described in the repository.

## [0.2.0] - 2026-10-19

### Added
- `INSTANCE_DIR`: a folder outside the repository whose `widget/`, `admin_panel/` and `config/`
  files replace the generic ones, so an organisation keeps its own pages, logos, footer,
  statistics and changelog apart from the code.
- Restricted area served from the address in `ADMIN_PATH` (not written in the code): the page only
  asks for the admin key; the panel, its script and its styles are delivered by the server only
  with a valid key.
- Failed admin keys are limited per address (`ADMIN_MAX_ATTEMPTS`, `ADMIN_LOCKOUT_SECONDS`): after
  five wrong keys the address gets 429 for 15 minutes, even with the right key; localhost is exempt.
- Questions that contain personal data are blocked before reaching the model (`PII_BLOCK`): fixed
  reply, nothing sent to the model, no text logged.
- "Apply" link next to "Forgot your password?" on the login screen of the widget.
- `CHANGELOG.md`, `version.json`, a CI check of their consistency and a job that creates the
  release from a protected `v*` tag.

### Changed
- `widget/` pages (`dashboard.html`, `pilot.html`, `come-funziona.html`), `site-footer.js`,
  `matomo.js` and the manifest are generic; the footer shows only release, last commit,
  accessibility link and the visitor's address.
- `matomo.js` does nothing unless `window.ZautteMatomo = { url, siteId }` is defined.
- `MIGRATED_DOMAINS` is empty by default.
- Unknown paths and methods answer with a plain HTML 404/405 page instead of FastAPI's JSON.
- The accessibility test covers the restricted area through its login page.

### Removed
- Municipality-specific files (logos, social image, privacy notice draft, pilot texts and the
  installation changelog) moved out of the repository.

## [0.1.1] - 2026-10-18

### Added
- `CITATION.cff`; this release is archived on Zenodo.
- Near-real-time news sync, single vote per answer, answers log, daily question limit.
- Footer IP links to RIPEstat; README sequence diagram.

### Changed
- Municipality-specific content (known facts, synonyms, offices, crawl overrides) moved out of the
  repository; `*.example.json` templates added.

## [0.1.0] - 2026-10-04

### Added
- First release (pilot project): crawler, embeddings with `bge-m3`, hybrid search, answers with
  cited sources, login, feedback, accessibility checks.

[Unreleased]: https://github.com/Antonio-Prado/zautte/compare/v0.4.2...HEAD
[0.4.2]: https://github.com/Antonio-Prado/zautte/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/Antonio-Prado/zautte/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/Antonio-Prado/zautte/compare/v0.3.2...v0.4.0
[0.3.2]: https://github.com/Antonio-Prado/zautte/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/Antonio-Prado/zautte/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/Antonio-Prado/zautte/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Antonio-Prado/zautte/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/Antonio-Prado/zautte/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Antonio-Prado/zautte/releases/tag/v0.1.0
