# Changelog

All notable changes to Zautte are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/) (`0.x` while it is a pilot: minor versions for new
features, patch versions for fixes). The version lives in `version.json`; CI fails when it does
not match the latest entry below. Changes that only touch an installation's own files (known
facts, texts, logos, see [Configuration](docs/configuration.md)) are not released.

## [Unreleased]

## [0.11.0] - 2026-10-27

### Changed
- The restricted area is organised in tabs instead of one long page: Overview (what needs attention first — open
  reports, applications to decide, maintenance — then the key numbers as tiles, questions by hour, most frequent
  questions), Reports, Messages, People (applications and users), System (service, index, token costs, crawl
  history), Chat and Changelog. The tab bar stays at the top and scrolls sideways on a phone; Reports and People
  carry the number of items waiting; the open tab is kept in the address (`#messages`), and the arrow keys move
  between tabs.
- The message log is a list of cards grouped by day instead of a three-column table that could not be read on a
  phone: search in questions, answers and request ids, filter by user, 30 cards at a time with «Show more», then
  older pages on request. The question opens the answer, with bold text and clickable addresses.
- Reports are resolved with a form in the page (a note of several lines with a counter up to 2000 characters,
  «Resolve and send email», «Resolve without email») instead of a browser prompt.
- Each tab is read when shown and refreshed only while on screen, nothing while the page is in the background.
  Before, the panel downloaded the last 500 messages with their retrieved passages every 30 seconds (about 1.2 MB),
  also on a phone and with the log out of sight.
- `GET /usage/messages` takes `offset` (to read the next page) and `retrieved=false` (leaves out the retrieved
  passages, about half of the payload). Without them the answer is the same as before.
- The changelog of the installation shows the newest 20 entries, the rest on request; the token history the latest
  10 questions. The users list and the applications load without a further click.

### Fixed
- The checkbox «also show accepted and rejected» was smaller than the 24 px a target needs (WCAG 2.2): the
  accessibility check now covers every tab of the restricted area, the tab keys and a 320 px screen.
- In the widget's login form, «Forgot password?» and «Apply» were about 23 px tall with some fonts, under the 24 px
  target size (WCAG 2.2): they are now at least 24 px. Found by the same check on the chat tab of the restricted area.

## [0.10.11] - 2026-10-27

### Fixed
- System prompts (Italian and English): told «you were wrong», the assistant now checks its previous answer
  without announcing it. With 0.10.10 it confirmed the right answer with its source, but opened with «I am
  checking my previous answer against the current context», talking about the context the prompts tell it
  never to mention.

## [0.10.10] - 2026-10-27

### Fixed
- System prompts (Italian and English): when the answer is not in the context, the assistant no longer sends the
  user to pages of the organisation's site, nor to its home page, to look for it. Those pages are what it reads,
  so the user would not find the answer there either. Asked which assessori belong to a party, it said it did not
  know and pointed to the page of the Giunta it had just read; asked how new smart bins open, it closed with
  «visit the Comune's site». Now it says plainly that it did not find the information and points to who can
  answer (office, phone, email). A link to a page is given only to do something (a form, a booking, an online
  service). The fallback contact no longer includes the site's address.
- System prompts: the assistant does not infer from silence. Asked whether a party was in the Giunta, it answered
  «no» because the party did not appear in the passages it had. It now says the pages do not show it, does not
  extend to one case what the pages say about another, and when in doubt says it did not find the information:
  better one answer fewer than a wrong or invented one.
- System prompts: told «you were wrong» with no reason, the assistant apologised, changed its answer and said it
  «had not yet received the context». It now checks the previous answer against the context, corrects only what
  is unsupported and confirms the rest with the source.

## [0.10.9] - 2026-10-26

### Fixed
- System prompts (Italian and English): a question that is incomplete or hastily written but has a clear
  topic is not treated as generic. Asked «Per effettuare il ritiro della materiale ingombrante», the assistant
  gave the full answer on bulky waste pickup but opened with «the question is a bit incomplete: do you want
  to book the pickup or take them yourself?». Now it answers on the topic without remarking on the question.

### Changed
- `PII_KEEP_PHONE_PREFIXES` documented as a comma-separated list where a whole number keeps only that number,
  for the public service companies the answers point to (e.g. waste collection).

## [0.10.8] - 2026-10-26

### Changed
- The sources listed under an answer are those the answer cites, not every passage given to the model:
  a source counts when the answer carries its link or its title, or a link written inside its passage (a
  known fact pointing to a page of its own); a bare host such as the site's home page cites nothing. When
  the answer cites none (it asks to be more specific, finds nothing, quotes only phone numbers) the known
  facts it was given are listed, otherwise nothing. Before, about six sources were listed under each answer
  while the text cited less than one on average, so old news items and unrelated files sat under correct
  answers. `answers.jsonl` keeps every retrieved source in `retrieved`, shown by `GET /usage/messages`;
  `scripts/eval.py` counts the retrieved passages from it.

## [0.10.7] - 2026-10-26

### Fixed
- System prompts (Italian and English): a question that names an everyday thing is no longer treated as
  generic. Asked «where can I pay the electricity bill?», the assistant replied that the question was
  generic and asked whether it meant the nursery fees or the cemetery votive lights, because both were
  among the retrieved passages. Now it answers the common meaning straight away and mentions services
  of the organisation with a similar name at most in one line at the end.

## [0.10.6] - 2026-10-26

### Fixed
- `POST /feedback/resolve` accepted a note of up to 2000 characters since 0.10.2, but still cut it at
  1000 before saving it and sending it in the acknowledgement email, so a longer note reached the person
  who reported truncated mid-word. The limit is now one constant, checked on the request only, and the
  note is saved and sent whole.

## [0.10.5] - 2026-10-26

### Changed
- `requirements-lock.txt`: orjson 3.13.0 (was 3.11.8).

### Fixed
- `scripts/setup_freebsd.sh` with a Rust older than 1.95 (needed by orjson 3.11.9 and later, which is built
  from source on FreeBSD): it installed with the constraint `orjson<=3.11.8`, which contradicts the version
  pinned in the lock as soon as that is newer, and pip refused the installation. It now installs from a copy
  of the lock where orjson is pinned to 3.11.8 with the hash of its source archive.

## [0.10.4] - 2026-10-26

### Fixed
- In-page chat of `dashboard.html`: after login the chat got only the height left under the title, the
  intro and the footer, and showed 3 lines of an answer on a phone, 4 on a larger phone and 6 on a
  1366×768 laptop. Now the chat is as tall as the window (at most 640 px, the widget default) and the
  page scrolls; when the chat does not fit, the widget brings it into view after login and when a
  question is sent (`inline` mode only). About 21 lines are visible on those screens.
- While an answer arrives the chat no longer follows its end down to the 👍 👎 buttons: it follows the
  text until the first line of the answer reaches the top of the area, then stays there, so the reader
  starts from the beginning.

## [0.10.3] - 2026-10-26

### Fixed
- The assistant could talk as if it were a person: asked for emotional support, it answered "I'm not
  the right person" and "you deserve real support, from a person in flesh and blood", with a feminine
  adjective in Italian, and said it was a program only when asked why it claimed to be a person. The
  system prompts now say it is an automated program that never says or implies otherwise (and, in
  Italian, speaks of itself in the masculine).
- When someone shares a personal difficulty, the answer now says plainly that the assistant cannot
  offer a listening ear, points to the services found in the retrieved content and suggests someone
  they trust or their family doctor; with an immediate danger it points to 112, the only number it may
  give without a source. Before, it gave helpline numbers from the model's own memory, which the rules
  forbid.

## [0.10.2] - 2026-10-25

### Changed
- The note sent with the resolution of a negative feedback (`POST /feedback/resolve`) can be up to
  2000 characters, not 1000: a reply that explains what was wrong and what the assistant answers now
  did not fit.

## [0.10.1] - 2026-10-25

### Fixed
- Crawler: a word in an inline tag (`<strong>Non</strong>`, a link, an emphasis) landed on a line of its
  own in the extracted text, and the line filter dropped it when shorter than 4 characters: a negation
  at the start of a sentence disappeared from the index ("Non si possono autenticare procure" was indexed
  as "si possono autenticare procure"). Inline tags are now unwrapped before the extraction, so their text
  stays in its sentence; non-breaking spaces become plain spaces. Pages are re-extracted at the next
  full sync.
- The unit test of the dynamic known facts failed on a runner up for less than a minute (the reread
  throttle compares `time.monotonic()` with 0).

## [0.10.0] - 2026-10-25

### Added
- Dynamic known facts: the API also reads `KNOWN_FACTS_DYNAMIC_FILE` (`data/facts/known_facts_dynamic.json`,
  same format as `known_facts.json` plus an `id` per fact), checks it once a minute and rereads it when it
  changes, without a restart. Jobs can keep facts that change over time up to date.
- `scripts.council_sessions`: hourly cron job (as the sync account) that reads the page of the council
  convocations (`council.convocations_url` in `crawl_extra.json`), works out the last session held and the
  next one convened, and writes the calendar as a dynamic known fact, with a warning that the dates listed
  under the previous year belong to that year. Until now the calendar was a hand-written known fact that went
  stale at every new session.

## [0.9.1] - 2026-10-25

### Fixed
- The phone numbers of the institution's own offices were masked as `[telefono]` in `answers.jsonl`
  (so in the message log of the restricted area) and in the conversation history sent back to the
  model on the next turns, while the visitor saw the real number. `PII_KEEP_PHONE_PREFIXES` lists the
  national prefixes (e.g. the switchboard range) that stay in clear, like `PII_KEEP_EMAIL_DOMAINS`
  does for institutional e-mail addresses; a question that quotes such a number is not refused by
  `PII_BLOCK` either.

## [0.9.0] - 2026-10-24

### Added
- Restricted area: the card "Volunteer applications" lists the applications received (`GET /applications`) and
  accepts or rejects each one from the browser, also on a phone (`POST /applications/accept|reject`); the
  decision asks for confirmation and accepting sends the invitation at once. Until now the decisions were
  only possible with `scripts.applications` on the server. The notification e-mail of a new application carries
  the link to the restricted area (`PUBLIC_BASE_URL` or the origin of `PILOT_LOGIN_URL`, plus `ADMIN_PATH`).
- Restricted area: the cards stack in one column on narrow screens.

## [0.8.4] - 2026-10-23

### Fixed
- `DAILY_LIMIT_EXEMPT` also matches the account id, which is what the session token carries (the name in the
  token is the display name, so an entry by short name did not match).

## [0.8.3] - 2026-10-23

### Added
- `DAILY_LIMIT_EXEMPT`: accounts (names or e-mails) not subject to the daily question limit, for test accounts;
  their questions are still logged and billed. `/auth/login` and `/auth/me` tell them `daily_limit: 0`.

## [0.8.2] - 2026-10-23

### Fixed
- Maintenance mode: the reason shown in the admin view of `/health` is a fixed description of the cause; the
  provider's error message goes to the log only (CodeQL: information exposure through an exception). The
  module keeps its state in one dictionary instead of module globals (CodeQL: unused global variable).

## [0.8.1] - 2026-10-23

### Changed
- The system prompts ask the model for bullet lists instead of Markdown tables: the widget renders lists, bold,
  headings and quotes but not tables, and the chat window is narrow on a phone.
- The system prompts ask the model, when a question is generic or ambiguous because its object is missing
  ("who is in charge?"), to ask for details in one or two lines instead of listing everything that might match.

## [0.8.0] - 2026-10-23

### Added
- Maintenance mode (`api/maintenance.py`): when the model provider refuses to serve (API credit exhausted,
  key not valid or without permission, payment required) the chat answers HTTP 503 with a neutral
  "service under maintenance" text for `MAINTENANCE_HOLD_SECONDS` (default 600) without calling the model,
  then tries again. The public `/health` only says `maintenance: true`; the cause goes to the log, to the
  admin view of `/health` (`maintenance_info`) and to the restricted area. The administrator gets an alert
  e-mail (`MAINTENANCE_ALERT_EMAIL`, default `FEEDBACK_NOTIFY_EMAIL`) at most once an hour and one e-mail
  when the service is back. The widget shows the notice when the chat opens, on a 503 and on a streaming
  error, and repeats it for a minute without calling the server.

## [0.7.41] - 2026-10-23

### Added
- The shared footer links the DOI of the software archive on Zenodo (concept DOI `10.5281/zenodo.23452491`,
  always the latest archived version), next to the release and the latest commit.

### Changed
- The footer calls the latest-commit link "Source".

## [0.7.40] - 2026-10-23

### Added
- The emails to participants (invitation, password reset, report resolved) end with an invitation to follow the
  organisation's news channel: new optional `NEWS_CHANNEL_URL` (a Telegram channel, a newsletter page) and
  `NEWS_CHANNEL_NAME` (how the channel is called in the sentence, default "canale Telegram"). Empty URL = no
  invitation, emails unchanged.

## [0.7.39] - 2026-10-22

### Changed
- The invitation email tells the participant what is asked of them: ask questions in their own words, rate every
  answer with 👍 / 👎, and after a 👎 say what is wrong and paste the link of the right page; it states the daily limit
  (`DAILY_QUESTION_LIMIT`) and the period of the pilot (new optional `PILOT_PERIOD`, free text). The greeting no
  longer assumes the participant's gender.

## [0.7.38] - 2026-10-22

### Changed
- The password page (`/auth/reset`) looks like the other pages: logo (`SITE_LOGO_URL`), the name of the organisation
  (`SITE_NAME`), links to "how it works" (`SITE_INFO_URL`) and to the privacy notice (`PRIVACY_NOTICE_URL`), and the
  shared footer (`/widget/site-footer.js`, the installation's own when it overrides it).
- The button of the participation declaration says "Entra nella sperimentazione" (the person has already asked to
  take part when applying), and the declaration states the daily limit of questions (`daily_limit` in `/auth/login`
  and `/auth/me`, from `DAILY_QUESTION_LIMIT`).

## [0.7.37] - 2026-10-22

### Added
- Applications to the pilot (`APPLY_ENABLED`): `POST /apply` records name, email, age (at least 18), district, a
  line of text and the two declarations in `applications.jsonl` (five per hour per client, hidden field against
  robots) and emails `APPLY_NOTIFY_EMAIL`; `GET /applications`, `POST /applications/accept` (creates the account
  and sends the invitation) and `POST /applications/reject` for the administrator, plus `scripts/applications.py`.
  Rows expire after `RETENTION_APPLICATIONS_DAYS` (90).

## [0.7.36] - 2026-10-22

### Added
- Widget option `applyUrl`: the "Apply" label of the login form becomes a link to the page where volunteers apply.

## [0.7.35] - 2026-10-22

### Added
- Participation declaration (`CONSENT_REQUIRED`): before the first question a logged-in user declares in the chat to
  be at least 18 years old and to have read the privacy notice (`PRIVACY_NOTICE_URL`, linked from the form) and asks
  to take part; recorded in `users.json` with date and `CONSENT_VERSION` (`POST /auth/consent`; `/chat` answers
  `403 consent_required` until then; `/auth/login` and `/auth/me` say whether it is still due).

### Changed
- The texts of a feedback expire after `RETENTION_FEEDBACK_TEXT_DAYS` (default: the same as the rows), no longer
  after the question text retention: a report is read and answered by a person, and a privacy notice may keep it
  longer than a question.

## [0.7.34] - 2026-10-22

### Security
- The sync jobs run under a memory ceiling (`ulimit -v`, `SYNC_MAX_VMEM_KB`, default 24 GiB): a hostile PDF or page
  that makes a parser blow up ends the job, not the API that shares the machine.
- The vector store backup runs as root into a folder the sync account cannot write: a bug or a compromise of a sync
  cannot delete the index and its backups together.
- `scripts/cron_setup.sh` is retired: it made root jobs write into a folder owned by the sync account.
- The API refuses to start when one of its data files belongs to another account or is a link.
- `.env.sync` is written with a private umask and ignored by git.

### Fixed
- The crawler writes `index.json`, `crawl_state.json` and `pdf_link_texts.json` atomically: a run stopped halfway no
  longer leaves a truncated file that breaks every later incremental crawl.
- The vector store is saved under a file lock (two writers, e.g. a sync and a script run by hand, no longer interleave)
  and is read against a stable set of files (if a sync renames a new generation in during the load, the load is
  repeated).
- The revocation list and the "resolved" marks are read and written under the file lock shared with the purge, so a
  logout or a mark cannot be lost to a concurrent rewrite.
- SIGTERM is left to uvicorn with a bounded graceful shutdown (`SHUTDOWN_GRACE_SECONDS`): `service chatbot stop`
  ends cleanly instead of with SIGKILL.
- The watchdog gives a freshly started service five minutes before judging it stuck.
- `sync_full.log` is rotated by newsyslog.

## [0.7.33] - 2026-10-22

### Security
- One expiry for every text a user typed or was answered (`RETENTION_USAGE_TEXT_DAYS`): `gaps.jsonl` entries never
  outlive it, and a feedback loses its question, answer excerpt, comment, links and author's name after it, keeping
  the vote, the dates and the ids (the panel counters do not change). Older "resolved" marks keyed by time and
  question are renamed accordingly.
- A feedback row records the user id only; the name is resolved when the panel or the notification shows it.
- Answers are masked like the questions before they are recorded and before they are kept in the conversation memory
  sent back to the model provider, in case the model echoed a personal datum the question filters let through.
- The gap log records the question as typed (masked), never the question rewritten with the previous turns; a
  `rewritten` flag says when retrieval used a rewrite. Deleting a question no longer leaves its text in the gap of the
  next one.
- The notification emails carry neither the question nor the reporter's name in the subject (subjects end up in mail
  logs), and the links reported by a colleague are marked as unverified.

## [0.7.32] - 2026-10-22

### Security
- A question or a comment containing a Unicode line separator (U+2028, U+2029, U+0085) is written to the JSONL files
  as a JSON escape, and the files are read line by line on `\n` only: such a record used to be split in two non-JSON
  fragments by the first rewrite (purge, deletion on request, a second vote) and then kept for ever, invisible and
  never expiring.
- A request without an admin key is never held up by the lockout: the public `/health` answer and the ordinary `403`
  come back whatever the state of the blocks, so a hundred wrong keys cannot take the health check down, and a wrong
  key on loopback cannot make the watchdog restart the service.
- The hosts an answer may link to are the site's own, those named in the known facts and those of the sources of the
  passages, no longer every host written inside the passages: a document published on an allowed site cannot make an
  address of its own clickable. Email addresses in answers follow the same rule.

### Fixed
- The watchdog keeps a restart or reload request that comes too soon and honours it once the minimum gap has passed;
  it used to delete it, leaving the API on the old index.
- PDF titles: the cache file names of 0.7.23 (24 hex digits) are recognised as such, as the older ones were, so a PDF
  without an internal title does not show its file name as title.

## [0.7.31] - 2026-10-22

### Changed
- The same unit test waits for the cancelled task with `gather` and asserts its state; closes the remaining code
  scanning note. No change to the program.

## [0.7.30] - 2026-10-22

### Changed
- A unit test is written without an empty `try` block; closes a code scanning note. No change to the program.

## [0.7.29] - 2026-10-22

### Changed
- Re-ranking: news items and their attachments get a small penalty (`NEWS_PENALTY`, 0.04), passages from retired
  domains a smaller one (`MIGRATED_PENALTY`, 0.03), as PDFs already did. On 100 questions with a known page the right
  page entered the first three passages for 3 more questions and the noise among the 7 fell by a fifth, with no change
  for 10 news questions. Documented in `docs/how-it-works.md`.

## [0.7.28] - 2026-10-22

### Fixed
- The answer given by `/chat/stream` was not always recorded in `answers.jsonl` (since 0.6.5): the write runs in a
  worker thread, and the response generator cancels the producing task right after the last event, so a write still
  pending at that moment was dropped. The write is now shielded from that cancellation (`api/aio.py`), with a test.

## [0.7.27] - 2026-10-22

### Security
- The firewall template (`scripts/pf.conf.example`) allows outbound traffic per account: the API account only HTTPS,
  SMTP submission and DNS; the sync account HTTP, HTTPS, SMTP submission and DNS; `ntpd` NTP and DNS; root and the
  administrator everything; the Ollama account and any other account nothing. A compromised service or dependency cannot
  reach other hosts. Documented in `docs/operations.md`.

## [0.7.26] - 2026-10-22

### Changed
- `scripts/inactive_users.py` reads the creation date of an account in a function of its own; closes a code scanning
  warning (empty `except`).

## [0.7.25] - 2026-10-22

### Security
- pypdf 6.21.1 (a JPEG image inside a PDF was loaded without validation).

## [0.7.24] - 2026-10-22

### Fixed
- The logout revocation list is read as empty when its file is empty (the file is created empty at startup); it used to
  log a warning at every read.

## [0.7.23] - 2026-10-22

### Security
- A feedback is marked resolved under its own id instead of time and question, so two reports with the same question in
  the same second can no longer resolve each other. Marks made before keep working; the panel sends the id.
- Cache files of the crawler are named with 96 bits of SHA-256 instead of 48 bits of MD5. A page or document is saved
  under the new name when it is saved again, and the old file is deleted at the end of the crawl, after the index and
  the state that name the new one are written; nothing is downloaded again because of this.
- The inbox ignores symbolic links.
- Addresses that carry an IPv4 address inside IPv6 (NAT64, 6to4, Teredo, IPv4-mapped) are public only if the IPv4
  address is public too; the crawler and the news sync no longer read proxy settings from the environment.
- `purge_logs` drops the expired entries of the logout revocation list. New accounts carry a `created` date, and
  `scripts/inactive_users.py` lists (and, on request, removes) accounts with no activity for a long time.
- `requirements-lock.txt` carries the SHA-256 of every file (`scripts/update_lock_hashes.py` regenerates them) and pip
  refuses anything else. CI installs from it, uses a pinned ruff, and the GitHub Actions are pinned by commit.
- `setup_sync_user.sh` changes the owner of directories and of files with a single name only, never through a link.

### Added
- Unit tests (`tests/unit`, pytest, run by CI) for personal data masking, client keys, the Content-Security-Policy,
  password hashes, the network policy, retired domains, prompt and ingestion hardening, feedback keys, the response cache
  and the crawler state.

## [0.7.22] - 2026-10-22

### Security
- A feedback keeps the question and the answer excerpt that the server logged for that answer, not the text sent by
  the browser (which is used only when the server has nothing, e.g. after the retention period).
- Personal data masking: tax codes written in groups (`RSSMRA 80A01 H501Z`), IBANs with hyphens, phone numbers after a
  dot (`tel.333…`) and foreign phone numbers are masked; a lower-case "mi chiamo mario rossi" is blocked like the
  capitalised form.
- The response cache keys on SHA-256, drops an entry after 6 hours and does not reuse an answer on another day.

## [0.7.21] - 2026-10-22

### Security
- `MIGRATED_DOMAINS` is compared with the host and the path of an address (a host also matches its subdomains; a path
  prefix matches on whole segments) instead of being looked for anywhere in the text: an address that merely mentions a
  retired domain in its query string is no longer treated as retired, and a similar name does not match.
- The news sync takes a post or feed item only if its link is a plain http(s) address on a host it is allowed to use.
- XML read by the news sync (feeds, Office attachments) is refused if it declares a document type or entities.
- `scripts/embed_check.py` saves and reads its sample without pickle.

## [0.7.20] - 2026-10-22

### Security
- New password hashes use scrypt with twice the memory (N=2^15). An older hash is stored again with the new parameters
  the first time its owner logs in; a stored hash asking for absurd parameters is refused instead of computed.
- The mailer refuses to log in to a relay (`SMTP_USER`) without `SMTP_SSL` or `SMTP_STARTTLS`, unless the relay is on
  the same machine: the password would travel in clear text.
- The subject of an email is a single line: a question with a line break no longer breaks the notification.
- The restricted area opens a link only if it starts with `http://` or `https://`.
- `setup_freebsd.sh` writes its temporary constraints file with `mktemp` instead of a predictable name in `/tmp`.

## [0.7.19] - 2026-10-22

### Changed
- The inline scripts of a page are found with a closing tag read the way a browser reads it (`</script foo>` ends a script
  too); closes a code scanning warning. The pages of this repository have no such tag, so no hash changes.

## [0.7.18] - 2026-10-22

### Security
- The widget pages, the password reset page and the login page of the restricted area carry a Content-Security-Policy
  (`api/csp.py`): scripts only from the same origin plus the inline scripts of the page as served, named by SHA-256 and
  computed from the file on disk (an installation's own pages need no change); the panel script that the restricted
  area delivers after login is named by hash too. An injected script is not run and cannot read the session token or
  the admin key. `CSP_MODE` (enforce, report-only, off), `CSP_EXTRA_HOSTS` (the Matomo server) and `FRAME_ANCESTORS`
  configure it. Every response also gets `Referrer-Policy` and `Permissions-Policy`.

## [0.7.17] - 2026-10-22

### Security
- The vector store is saved as a set: the three files are written aside first, renamed one after the other, and a
  `manifest.json` (size and SHA-256 of each) is written last. At load the files must match it, so files of different
  generations left by an interrupted save are detected instead of silently paired.
- A store that exists but cannot be read (or does not match its manifest) is no longer treated as empty and overwritten by
  the next sync: the API works with an empty store, logs the error, and writing refuses until a backup is restored or the
  store is emptied on purpose. `scripts/reembed.py apply` refreshes the manifest.
- Vectors that are not numbers or have a length above 1 (an overflow of the model, damage, tampering), which would have
  ranked first for every question, are neutralised or brought back to unit length at load.

## [0.7.16] - 2026-10-22

### Security
- A download is dropped after `CRAWL_MAX_DOWNLOAD_SECONDS` (300): a server that sent one byte every few seconds used to be
  kept for ever, whatever the size limit.
- PDF extraction reads at most `PDF_MAX_PAGES` (2000) pages and keeps at most `PDF_MAX_CHARS` (5,000,000) characters, and
  stops after `PDF_MAX_SECONDS` (180), also in the middle of a page that never finishes, keeping what was read.
- The crawler follows at most `CRAWL_MAX_QUERY_VARIANTS` (25) query-string variants of one path (endless calendars and
  filters) and tests its queue in constant time instead of scanning a list.
- The sync wrappers run the job under `timeout` (news 25 min, incremental 16 h, full 40 h; `SYNC_TIMEOUT` changes it),
  so a hung job no longer holds the lock for ever and makes every later sync skip.

## [0.7.15] - 2026-10-22

### Security
- The keyword search takes each word of a question once and at most 32 words. A question made of hundreds of repeated
  words used to make the scoring loop over every passage for each of them (about 2 s per 20,000 passages, so tens of
  seconds on the full index, holding up the whole service); it is now a few milliseconds.

### Fixed
- Office attachments (DOCX, ODT) of news items were treated as inbox documents, so a corrected version of the same file
  was added next to the old one and a withdrawn file never left the index. Only documents without an origin (inbox
  documents that predate the marker) and those marked `origin=inbox` are kept by the syncs now; news attachments are
  replaced like any other news chunk, and the next news sync fixes the existing ones.

## [0.7.14] - 2026-10-22

### Changed
- The request size limit returns in a uniform way (closes a code scanning note); no change in behaviour.

## [0.7.13] - 2026-10-22

### Fixed
- `link_hosts` no longer includes the example address used in the instructions to the model (`esempio.it`), which
  would have let the widget link to it.

## [0.7.12] - 2026-10-22

### Security
- Against hostile text in indexed pages and files: the instructions now say that the passages are data and not
  commands, that addresses, IBANs, phone numbers and emails may be given only if they appear in them, and that no
  official acts are drafted; a passage can no longer imitate the `[Fonte n: ...]` labels and `---` separators that
  frame the passages, and titles, categories and dates in the labels are cut and checked.
- When nothing relevant is found, `/chat` also answers with the fixed text and no model call (it used to ask the model, and
  cache the answer), as `/chat/stream` already did.
- The answer carries `link_hosts` (a first event of the stream, a field of `/chat`): the site's hosts and those that appear
  in what the model was given, never one that only the user's question mentions. The widget makes a link only to those
  hosts, writes the host out for the others, and shows the real address when a link's text names a different one.
- Text a visitor cannot see is no longer indexed: tiny, off-screen, clipped or zero-size elements and `sr-only`/`d-none`-style
  classes in pages and in WordPress posts; hidden and deleted text in DOCX, tracked changes, hidden text and comments in
  ODT; invisible Unicode characters (format controls, the "tag" block, private use) in every document and title.

## [0.7.11] - 2026-10-22

### Security
- The sync, re-embedding, cleanup, inbox and indexer scripts started as root now become the sync account (`SYNC_USER`,
  default `zsync`) before reading `.env`, and use `.env.sync`; root no longer writes in folders that the sync account
  can plant links in. Without that account they keep running as root, with a warning.
- The vector store files are written through a temporary file created exclusively with a random name (mode 644)
  instead of the predictable `*.tmp` that a link could replace.
- The indexer and `cleanup_index.py` use only the files that really lie in the crawl cache and the documents folder,
  whatever `index.json` says (it is written by the sync account), and no longer delete or read paths outside them.
- The watchdog honours a restart request at most once every 10 minutes and a reload every 2, and ignores a request file
  that is not a regular file, so a compromised sync account cannot keep the API restarting.

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
