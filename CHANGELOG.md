# Changelog

All notable changes to Zautte are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/) (`0.x` while it is a pilot: minor versions for new
features, patch versions for fixes). The version lives in `version.json`; CI fails when it does
not match the latest entry below. Changes that only touch an installation's own files (known
facts, texts, logos, see [Configuration](docs/configuration.md)) are not released.

## [Unreleased]

### Changed
- Documentation brought in line with 0.2.0: restricted area and lockout (`ADMIN_PATH`, `ADMIN_MAX_ATTEMPTS`, `ADMIN_LOCKOUT_SECONDS`), `INSTANCE_DIR`, generic pages and footer, releasing; the sequence diagram in the README now shows sign-in, the block on questions with personal data, the daily limit and the fixed replies; news sources are described generically (WordPress, RSS) instead of as a municipal bulletin.

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

[Unreleased]: https://github.com/Antonio-Prado/zautte/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Antonio-Prado/zautte/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/Antonio-Prado/zautte/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Antonio-Prado/zautte/releases/tag/v0.1.0
