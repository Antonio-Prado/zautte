# Changelog

All notable changes to Zautte are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/) (`0.x` while it is a pilot: minor versions for new
features, patch versions for fixes). The version lives in `version.json`; CI fails when it does
not match the latest entry below. Changes that only touch an installation's own files (known
facts, texts, logos, see [Configuration](docs/configuration.md)) are not released.

## [Unreleased]

## [0.7.10] - 2026-10-22

### Security
- Per-account limits: after `LOGIN_MAX_FAILURES` (10) wrong passwords in `LOGIN_LOCK_SECONDS` (15 minutes) an account
  refuses logins with the same answer as a wrong password, so passwords of known accounts cannot be sprayed from many
  addresses; at most `RESET_EMAILS_PER_HOUR` (3) reset emails are sent per account. `scripts/adduser.py` asks for
  passwords of at least 10 characters, as the reset page does.
- Request bodies over `MAX_BODY_BYTES` (128 KB) are refused (413) before they are buffered.
- The admin lockout no longer exempts loopback: a right key on loopback always passes (sync scripts, watchdog), a wrong
  one is counted, so a local proxy that shows every client as 127.0.0.1 cannot switch the lockout off. The API refuses
  to start when `FORWARDED_ALLOW_IPS` trusts every address (`*`, `0.0.0.0/0`, `::/0`).

## [0.7.9] - 2026-10-22

### Security
- Rate limits, the admin lockout and the quota without login count an IPv6 client by its /64 network (an IPv4-mapped
  address as the IPv4 one) instead of the exact address, which gave anyone with a normal IPv6 connection a fresh
  budget on every request. A ceiling of 100 wrong admin keys from all clients together refuses further requests from
  outside until the window ends, and active blocks are no longer pushed out of the table by a flood of new addresses.
- The API process creates its files with mode 600 and, at start, restricts the data files it owns (and creates the
  missing ones and their lock files exclusively), so an account in the shared data group, such as the sync account,
  cannot read questions, answers or feedback or create those files first. Problems it cannot fix are logged.
- Feedback comments, the answer excerpt and links go through the personal-data filters: a comment with personal data is
  replaced by a notice, the rest is masked, links that contain any are dropped.
- The API refuses to start when `users.json` has accounts but `AUTH_ENABLED` is not true (a lost or unreadable `.env`
  used to open the bot to everybody); `ALLOW_ANONYMOUS=true` says that is intended.

## [0.7.8] - 2026-10-22

### Changed
- Removed the `secondaryColor` widget option, which was defined but never used.
- Corrected the comment on `PDF_PENALTY` in `api/rag.py`: inbox PDFs are penalised like any other PDF, only
  inbox TXT and DOCX files are not; installation-specific figures removed from it.

## [0.7.7] - 2026-10-22

### Changed
- Documentation reread against the code and corrected: the sync environment, the privilege model, the unprivileged
  service account required by the rc.d script, request files for the watchdog, the manual sync lock, the data
  folder paths, new and missing settings in `.env.example` and the configuration guide, the chat responses
  (`cid`, `blocked`, 429 `busy`), the feedback `rid` rules, the crawler network policy, the reference figures and
  installation-specific wording, and the contributing guide (lint, accessibility check, version check).
- `scripts/setup_sync_user.sh` finds the data folder from the installation (it follows a symlink) instead of a
  fixed path; the `newsyslog` sample no longer lists logs that nothing writes; `scripts/cron_setup.sh` says it
  must run as root.

## [0.7.6] - 2026-10-22

### Changed
- The access log with client addresses (`chatbot.log`) is now kept for 90 days by default in the newsyslog
  configuration (it was 365); the operations guide says how to match it to the privacy notice.

## [0.7.5] - 2026-10-22

### Changed
- The admin key guard keeps the time of its last cleanup in a state dictionary instead of a module variable
  (closes a code scanning note); no change in behaviour.
- Documentation aligned with 0.7.4: the sync environment allowlist, new modules and scripts in the architecture
  tree, the limit on concurrent answers, the reset link in the fragment, the admin key requirements and `PII_BLOCK`.

## [0.7.4] - 2026-10-22

### Security
- The environment file of the sync account (`.env.sync`) is built from an allowlist of the settings the jobs need
  (`scripts/make_sync_env.py`, read with the same parser as the application) instead of removing a list of secrets,
  so SMTP credentials and `export NAME=...` lines can no longer slip into it. Summary emails of jobs run as that
  account are no longer sent; run by root they still are.
- `ADMIN_API_KEY` must be at least 16 characters and not a placeholder: the API refuses to start with a weak
  one, and an unusable key keeps the admin endpoints closed. `.env.example` ships it empty.
- The rc.d script no longer defaults to running the API as root: set `chatbot_user`, or `chatbot_allow_root="YES"`
  to keep the old behaviour knowingly.
- An unreadable or invalid `users.json` now refuses every session instead of keeping the last good copy.
- Concurrent answers are limited (`LLM_MAX_PER_USER`, default 2, and `LLM_MAX_CONCURRENT`, default 8): the request
  is refused with a clear message before it uses any daily quota.
- Deleting all the questions of a user (`POST /usage/forget` with `uid` and `all`) also removes their usage
  records from `stats.json`.

## [0.7.3] - 2026-10-21

### Added
- `scripts/rotate_ollama_log.sh` rotates the Ollama logs by copy and truncate (newsyslog cannot, as the ollama
  rc.d script starts `daemon` without `-H`); documented in the operations guide.

## [0.7.2] - 2026-10-21

### Fixed
- The crawler and the news sync no longer fail on a response whose headers contain non-ASCII characters (for
  example a `Content-Disposition` with an accented file name): the headers of the checked response are now copied
  as raw bytes. The bug, introduced with the pinned connections in 0.6.4, made the incremental sync skip about
  280 attachment downloads in one run.

## [0.7.1] - 2026-10-21

### Security
- The API listens on `API_HOST` (comma-separated, default local only `127.0.0.1,::1`) and `API_PORT`; `start.sh` no
  longer forces `0.0.0.0` and `::`. An installation that wants the proxy to reach it sets `API_HOST=0.0.0.0,::` in
  `.env` and restricts the port with the firewall.
- With `AUTH_ENABLED=true`, an `AUTH_SECRET` shorter than 16 characters stops the API from starting, and tokens are
  never signed or verified with a weak key.
- The password reset link carries the token in the fragment (`/auth/reset#token=...`), which browsers do not send
  to servers or proxies, so it cannot end up in their access logs; links sent before still work.
- `PII_BLOCK` no longer depends on `PII_REDACTION` and also stops a person's address at an institutional domain
  (`name.surname@`), while office addresses (`protocollo@`, `ufficio.tributi@`) still pass.
- The vector store backups are created readable by their owner only.

## [0.7.0] - 2026-10-21

### Security
- The sync jobs (crawler, indexer, news) can run without root, as a dedicated account: new
  `scripts/setup_sync_user.sh`. The account owns only the folders it writes and reads a copy of `.env` without
  the API secrets (`ZAUTTE_ENV_FILE`), so a flaw in a PDF, Office or HTML parser no longer gives root. The jobs
  cannot restart or reload the API themselves any more: they leave a request file in `SYNC_CONTROL_DIR`
  (`/var/db/zautte-sync`) and the root watchdog acts on it (reload through `/admin/reload`, or
  `service chatbot restart` instead of killing the process).
- Dependencies are pinned to exact versions in `requirements-lock.txt` (generated with `pip freeze`; `requirements.txt` keeps
  the minimum versions that Dependabot updates), and the installation guide installs from it.

## [0.6.7] - 2026-10-21

### Security
- `purge_logs` and `adduser` started as root now become the service account for real: it is named by `SERVICE_USER`
  (or, without it, is the owner of the existing data files), the groups are reset, and the script stops if it is still
  root afterwards or can get root back. In 0.6.6 the switch did nothing when `data/` belonged to root.
- A password reset link is checked again inside the lock of `users.json`, right before the write: two requests
  with the same link can no longer both set a password.
- `adduser --send-email` on an existing user no longer replaces the password or ends the open sessions: they change
  only when the user follows the link. The public address for the link is checked before anything is saved.

### Fixed
- The question embedding (a blocking call to Ollama) runs in a worker thread, four at a time, so a slow embedding no
  longer freezes the API, `/health` included.
- The cached answers are dropped when the index is reloaded, so a removed or updated document is no longer served
  from the cache.
- `stats.json` is written atomically.

## [0.6.6] - 2026-10-21

### Security
- A `.env` file can no longer set variables that steer the dynamic loader, the interpreter or the lookup of programs
  (`LD_*`, `PYTHON*`, `PATH`, ...), and the sync jobs start `pgrep` by its full path with a fixed environment. The guide
  now gives `.env` to `root:zautte`, mode 640: the service account reads it but cannot change what the root jobs load.
- An ODT/DOCX attachment whose XML expands to more than 50 MB is refused before it is read (a small zip could expand
  to gigabytes).
- `purge_logs` and `adduser`, when started as root, switch to the owner of the data files before reading or writing
  anything (new `api/privdrop.py`), so root never works on files the service account controls.
- The FreeBSD guide now gives the data folder to root (group `zautte`, mode 1770): with the sticky bit the service
  account can still create and replace its own files but can no longer rename the root-owned folders (vector store,
  crawl cache, documents, inbox) that the sync jobs write to. Before, owning the folder was enough to swap them.

## [0.6.5] - 2026-10-20

### Security
- Maintenance scripts that run as root in the data folder (`purge_logs`, `forget`, `adduser`) no longer follow a
  symbolic link planted there by the service account. Lock files are opened with `O_NOFOLLOW` and checked to be
  regular files; a file is chowned only when this process has just created it, through its descriptor.
- Temporary files for `users.json`, the feedback file, the resolved marks, the revoked tokens and the purge are
  created exclusively with a random name (`mkstemp`); permissions and owner are set through the open descriptor, and a
  target that is not a regular file is refused (new `api/safefile.py`).
- A lock that cannot be taken now stops the operation (OSError) instead of going on without it.
- The table of blocked addresses of the admin key check is swept once a minute and capped at 10,000 entries, so a
  scan from many addresses (IPv6, for instance) cannot make it grow without limit.

### Fixed
- Writes to the usage, answer, gap and feedback files made from the request handlers run in a worker thread, so a
  long rewrite of one of those files (which holds its lock) no longer freezes the whole API.

## [0.6.4] - 2026-10-20

### Security
- Downloads connect to the address that was checked, not to a second lookup of the name (the `Host` header and the
  TLS name stay those of the site, so certificates are verified as usual): a name that changes its answer between the
  check and the connection (DNS rebinding) reaches nothing. Redirects are followed by the code itself, one hop at a
  time, each checked like the first request. This replaces the check made after the connection (0.6.3).
- Compressed bodies are decoded in 1 MB steps and refused as soon as the decoded size passes the limit, so a tiny gzip
  that expands to gigabytes no longer takes the memory; only gzip and deflate are accepted.
- Sessions created before `auth_version` and invalidated by a password change stay invalidated (`pw_changed` is still
  checked next to `auth_version`).
- `/auth/login` checks the password in a worker thread, four at a time, so a burst of logins does not stall the API.
- Deleting questions by `rid` clears only the conversations of the users who asked them (it cleared everybody's).
- The statistics expire when the API starts and every six hours, not only when they are saved, and the activity view
  never shows expired data.
- With login enabled, `POST /feedback` needs the id of an answer that was given to the same user.

## [0.6.3] - 2026-10-20

### Security
- `adduser --send-email` no longer emails a password: it creates the user with a password nobody knows and sends a
  single-use link (valid `--invite-hours`, default 72) to choose it. Needs `PUBLIC_BASE_URL` or `PILOT_LOGIN_URL`.
  `--password` is documented as something to avoid (shell history, process list).
- Sessions end when the password changes through a version counter (`auth_version` in `users.json`, repeated in the
  token) instead of comparing timestamps: a token issued in the same second as the change is now refused too.
- `users.json` is written atomically (temporary file, fsync, rename) and every change is made under a file lock.
- The network policy of the crawler also checks the address the connection really went to (a name that resolved to
  a public address at the check and to an internal one at the connection is refused before anything is read), caches
  the DNS answer for a minute only, and `CRAWL_PRIVATE_OK_HOSTS` matches exact host names, not subdomains.
- Removed the now unused `send_credentials` mail.

## [0.6.2] - 2026-10-20

### Security
- The access log hides the value of `token=` and `key=` in the query string, so the password-reset token no
  longer ends up in `chatbot.log` (kept for a year).
- The extraction of a page drops what a visitor cannot see (`hidden` attribute, `display:none`,
  `visibility:hidden`, `font-size:0`, `opacity:0`, text moved off-screen): it could carry instructions for the
  model. `aria-hidden` is kept, since it hides decorations from screen readers, not from the eye.
- Appending to and rewriting the data files (`usage`, `answers`, `gaps`, `feedback` `.jsonl`) takes a shared lock
  (`api/filelock.py`, `<name>.lock` next to the file): a line written while `purge_logs` or `forget` rewrote the
  file could be lost.
- Every response to a request that carries `X-Admin-Key` or `Authorization` gets
  `Cache-Control: private, no-store`.

## [0.6.1] - 2026-10-20

### Added
- `CRAWL_PRIVATE_OK_HOSTS`: allowed hosts that are let through although they resolve to a private address
  (split-horizon DNS, where the server sees an internal address for one of its own sites).

## [0.6.0] - 2026-10-20

### Security
- The server keeps the last turns of each conversation (`api/conversations.py`: in memory, two hours, tied to the
  user, lost on restart). The widget no longer sends the history, only the conversation id (`cid`) that came with
  the previous answer; the `history` field of `/chat` and `/chat/stream` is ignored. Before, nothing proved that a
  message with the role "assistant" had been written by the assistant. Deleting on request clears the conversations.
- Sources are links only when their address is `http(s)`; an inbox document whose `source_url` has another scheme
  gets the default `file://` name instead.

### Changed
- `docs/privacy-and-security.md`: the index is one and public, with no per-document permissions; only public
  documents belong in the inbox.
- The masking of the bot's messages in the history (0.5.5) is gone with the history itself.

## [0.5.7] - 2026-10-20

### Security
- One address policy for the crawler, the PDF links and the news sync (`crawler/netpolicy.py`): the host must be
  an allowed domain or one of its subdomains (before, `CRAWL_ALLOWED_DOMAINS` was matched as a substring, so
  `www.example.it.attacker.invalid` passed), with no credentials in the address (PDF links were not checked for
  `@`), no IP literals, only default ports, and no host that resolves to a private address
  (`CRAWL_BLOCK_PRIVATE_IPS`). Items of the RSS feeds are checked before they are downloaded.
- Redirects are checked hop by hop, so a page on an allowed host can no longer lead the crawler, or the text
  stored under its address, to another site.
- Downloads are read in chunks and stop at `CRAWL_MAX_PAGE_MB` (pages, feeds; default 20) or
  `CRAWL_MAX_FILE_MB` (PDFs and attachments; default 100); a larger body is skipped with a warning.

## [0.5.6] - 2026-10-20

### Security
- Login tokens last 7 days by default (`AUTH_TOKEN_TTL_DAYS`, was 30): people sign in again once a week.
  Installations that set the variable keep their value.
- The restricted area keeps the admin key in `sessionStorage` (gone when the tab closes) instead of
  `localStorage`, and removes any copy left there. The key has to be typed again in each new tab.
- The restricted-area page sends a `Content-Security-Policy` that forbids framing it, `<base>` and plugins,
  and every response carries `X-Content-Type-Options: nosniff`.

## [0.5.5] - 2026-10-20

### Security
- The conversation history sent by the client is masked in the bot's messages too (tax code, IBAN and
  payment card; emails and phone numbers stay, being those of the offices), not only in the user's.
- The most frequent questions and the per-user token records in `stats.json` now expire after
  `RETENTION_STATS_DAYS` (default 90; 0 = never), applied by the API every time it saves the statistics.
- `POST /feedback` refuses (403) a vote on an answer that was given to another user.

### Changed
- `docs/operations.md`: the `purge_logs` line is in the production crontab example, commented out until
  the retention periods are agreed with the data protection officer.

## [0.5.4] - 2026-10-20

### Security
- A session is now refused when `users.json` is empty or cannot be read (the last good copy is used
  while the file is unreadable, so a half-written file does not log everybody out).
- The password reset link is built only from `PUBLIC_BASE_URL` (new) or the origin of `PILOT_LOGIN_URL`,
  never from the `Host` header of the request (reset poisoning); without either, no link is sent.
- `/auth/forgot` sends the email after the reply, so the reply takes the same time for registered and
  unregistered addresses and a slow mail server cannot hold up the API.

## [0.5.3] - 2026-10-20

### Security
- The rc.d script can run the API as an unprivileged account: set `chatbot_user` in `/etc/rc.conf` and the
  `daemon(8)` supervisor starts the process with `-u` (default `root`, so existing installations are
  unchanged). New section "Running as an unprivileged user" in `docs/operations.md` with the account, the
  ownership of code and data, and the group of the sync log (also in `newsyslog-chatbot.conf`).

## [0.5.2] - 2026-10-20

### Security
- "Forgot password" no longer changes the password straight away: it emails a single-use link (30 minutes)
  to a page where the person chooses the new password, and the password changes only when the link is
  used. Before, anybody who knew an address could lock that person out by requesting resets. New
  endpoints `GET /auth/reset` and `POST /auth/reset`; the reset email no longer contains a password.

## [0.5.1] - 2026-10-20

### Security
- Sessions can now be ended: `POST /auth/logout` revokes the token (the widget calls it on sign-out), and
  a token also stops working when its user is removed or the user's password changes. Until now a token
  stayed valid until expiry whatever happened. New file `data/revoked_tokens.json`; tokens carry `iat` and
  `jti` claims (older tokens keep working until they expire, and can still be revoked).

## [0.5.0] - 2026-10-20

### Added
- New widget option `applyNote`: an optional small note shown under the "Apply" label of the login form
  (for example, that applications are not open yet). Empty by default.

## [0.4.3] - 2026-10-20

### Security
- Admin panel: the most frequent questions, the crawl timeline, the token table and the user table were
  inserted into the page without HTML escaping, so a question containing markup could run script in the
  administrator's browser (stored XSS). All values are now escaped.

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

[Unreleased]: https://github.com/Antonio-Prado/zautte/compare/v0.6.4...HEAD
[0.6.4]: https://github.com/Antonio-Prado/zautte/compare/v0.6.3...v0.6.4
[0.6.3]: https://github.com/Antonio-Prado/zautte/compare/v0.6.2...v0.6.3
[0.6.2]: https://github.com/Antonio-Prado/zautte/compare/v0.6.1...v0.6.2
[0.6.1]: https://github.com/Antonio-Prado/zautte/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/Antonio-Prado/zautte/compare/v0.5.7...v0.6.0
[0.5.7]: https://github.com/Antonio-Prado/zautte/compare/v0.5.6...v0.5.7
[0.5.6]: https://github.com/Antonio-Prado/zautte/compare/v0.5.5...v0.5.6
[0.5.5]: https://github.com/Antonio-Prado/zautte/compare/v0.5.4...v0.5.5
[0.5.4]: https://github.com/Antonio-Prado/zautte/compare/v0.5.3...v0.5.4
[0.5.3]: https://github.com/Antonio-Prado/zautte/compare/v0.5.2...v0.5.3
[0.5.2]: https://github.com/Antonio-Prado/zautte/compare/v0.5.1...v0.5.2
[0.5.1]: https://github.com/Antonio-Prado/zautte/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/Antonio-Prado/zautte/compare/v0.4.3...v0.5.0
[0.4.3]: https://github.com/Antonio-Prado/zautte/compare/v0.4.2...v0.4.3
[0.4.2]: https://github.com/Antonio-Prado/zautte/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/Antonio-Prado/zautte/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/Antonio-Prado/zautte/compare/v0.3.2...v0.4.0
[0.3.2]: https://github.com/Antonio-Prado/zautte/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/Antonio-Prado/zautte/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/Antonio-Prado/zautte/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Antonio-Prado/zautte/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/Antonio-Prado/zautte/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Antonio-Prado/zautte/releases/tag/v0.1.0
