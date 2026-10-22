<sub>[Zautte](../README.md) › [Documentation](README.md) › Operations</sub>

# Operations

Running Zautte in production: scheduled jobs, the FreeBSD service, monitoring and troubleshooting.

## Periodic Operations

### Orchestrator `scripts/sync.py`

Single command to coordinate crawl + indexing:

```sh
# Full crawl + re-index of everything: the index is not cleared and stays usable;
# sources no longer on the site are removed (except MIGRATED_DOMAINS)
python -m scripts.sync full

# Incremental update: re-indexes changed pages only, removes stale HTML sources
python -m scripts.sync incremental

# Process inbox folder only
python -m scripts.sync inbox

# Clear the store and re-index from data/crawl_cache/ without crawling
# (see "Changing chunking configuration")
python -m scripts.sync full-index

# Re-embed only the chunks with a zero vector (failed embeddings)
python -m scripts.sync reembed

# News published since the last run (see "News in near real time" below);
# --backfill indexes the whole archive of the news sources (one-off)
python -m scripts.sync news [--backfill]
```

Every mode except `news` ends by killing the `uvicorn api.main:app` processes so that the API reloads the vector store; the watchdog starts the service again within a minute. `news` instead asks the running API to reload the store from disk (`POST /admin/reload`, no restart, no 502). The sync log is `/var/log/chatbot-sync.log`. Run as the unprivileged `zsync` account (see [Running the sync jobs without root](#running-the-sync-jobs-without-root)), the syncs leave the reload or restart to the watchdog.

### News in near real time (`scripts/news_sync.py`)

The weekly crawl leaves a news item out of the index for up to seven days. `scripts.sync news` runs every 15 minutes from cron and reads the sources configured in `config/crawl_extra.json` under `news` (the file is specific to each installation; `config/crawl_extra.example.json` shows the format). Two kinds are supported:

- a WordPress news site, such as a municipal bulletin (`wordpress_api`): WordPress REST API `wp/v2/posts`, full article text, date and categories; the state keeps the latest `modified` seen, so a run without news costs two HTTP requests and does not load the vector store;
- a news feed of the institutional site (`rss`): title, link and date.

For every new or modified item it indexes the article (text prefixed with "Notizia del <date> (<categories>)"), the site pages linked in the article (the twin news page, a service page: same text and metadata the crawler would produce, so the weekly crawl finds the chunks already there) and the PDF, ODT and DOCX attachments linked by the article or by those pages (downloaded to `data/documents/` like the crawler's PDFs; link texts feed the readable PDF titles). All these chunks carry `origin: news`: the incremental and full syncs never remove them as stale (news pages are not always reached by the crawl), but the crawler or a later news run can still replace them when the source changes. The state lives in `data/crawl_cache/news_state.json`; `--backfill` ignores it and indexes the whole archive (a few thousand articles: 1-2 hours), then, when run as root, emails a summary to `FEEDBACK_NOTIFY_EMAIL`.

`scripts/news_sync.sh` is the cron wrapper: same lock as the weekly syncs, so while one of them is running the news run just skips.

Only the cron wrappers (`incremental_sync.sh`, `full_sync.sh`, `news_sync.sh`) take the lock (`/var/db/zautte-sync/sync.lock`, or `/var/run/chatbot-sync.lock` when that folder does not exist). The wrappers also stop a run that takes too long (`timeout`, then the lock is released): `SYNC_TIMEOUT` seconds, by default 25 minutes for the news, 16 hours for the incremental sync and 40 hours for the full one; set it in the cron line to change it. Two syncs writing the store at the same time can corrupt it, so run manual syncs under the same lock:

```sh
LOCK=/var/db/zautte-sync/sync.lock; [ -d /var/db/zautte-sync ] || LOCK=/var/run/chatbot-sync.lock
lockf -t 0 $LOCK venv/bin/python -m scripts.sync <mode>
```

During long runs the vector store is saved to disk every 30 minutes (checkpoint) and at the end.

### `/etc/crontab` — production schedule

The sync jobs download and parse documents from the Internet, so they should not run as root: they run as the account `zsync` (see [Running the sync jobs without root](#running-the-sync-jobs-without-root)). The watchdog and the log rotation stay root. In production they run from `/etc/crontab`:

```
# minute hour mday month wday who  command
*        *    *    *     *    root /opt/chatbot/scripts/watchdog.sh
0        2    *    *     *    zsync /opt/chatbot/scripts/backup_vectorstore.sh
30       2    *    *     1    zsync /opt/chatbot/scripts/incremental_sync.sh >/dev/null 2>&1
0        3    1    *     *    zsync /opt/chatbot/scripts/full_sync.sh >/dev/null 2>&1
*/15     *    *    *     *    zsync /opt/chatbot/scripts/news_sync.sh >/dev/null 2>&1
#15      2    *    *     *    root cd /opt/chatbot && venv/bin/python -m scripts.purge_logs >/dev/null 2>&1
```

`purge_logs` also drops the expired entries of the logout revocation list (`revoked_tokens.json`). Accounts that stopped asking questions are not removed by themselves: `venv/bin/python -m scripts.inactive_users --days 180` lists them (a new account carries a `created` date; one without it and without any question is listed apart and never removed) and `--remove` deletes them after asking.

The last line is commented out on purpose: `purge_logs` deletes data for good, so turn it on (and try `--dry-run` first) only when the retention periods (`RETENTION_*_DAYS`) have been agreed with the data protection officer and written in the privacy notice. Until then nothing is deleted automatically (the statistics expire by themselves, see `RETENTION_STATS_DAYS`).

| Schedule                       | Script                  | Description |
|--------------------------------|-------------------------|-------------|
| every minute                   | `watchdog.sh`           | Restarts the API if it is down or unresponsive; reloads or restarts it when a sync asks for it |
| 02:00 every night              | `backup_vectorstore.sh` | Compressed vector store backup |
| 02:30 every Monday             | `incremental_sync.sh`   | Incremental sync; skipped if another sync holds the lock |
| 03:00 on the 1st of each month | `full_sync.sh`          | Full sync; waits up to 12 hours for the lock, output in `/var/log/chatbot/sync_full.log` |
| every 15 minutes               | `news_sync.sh`          | New articles and site news with their attachments; hot reload of the API; skipped while another sync holds the lock |

Two jobs of `scripts/cron_setup.sh` below are **not** in the production crontab: `sync inbox` is run by hand after dropping documents into `data/inbox/` (nobody does that continuously, and run as root it restarts the API like the other syncs), and `purge_logs` stays off until the retention periods are approved by the data protection officer (try it with `--dry-run`; to enable it add `15 2 * * * root cd /opt/chatbot && venv/bin/python -m scripts.purge_logs >/dev/null 2>&1`).

`sync.py` also prints its log to stdout: without `>/dev/null`, cron tries to mail it and, with no local mailer, the job ends with exit 120.

### `scripts/cron_setup.sh`

An alternative for installations that run the service as a dedicated `chatbot` user (production does not: it uses the `/etc/crontab` above). The script (run it as root: `crontab -u` requires it) adds to the crontab of the user `chatbot` the incremental and full sync above, plus:

| Schedule                          | Command      | Description |
|-----------------------------------|--------------|-------------|
| every 30 min, 08:00–18:30 Mon–Fri | `sync inbox` | Processes inbox documents (no lock). As user `chatbot` it cannot leave the restart request for the watchdog, so the new documents are served only after the next API restart |
| 02:15 every day                   | `purge_logs` | Applies the log retention periods |
| 04:00 every Sunday                | `find … -size +10M` | Compresses logs larger than 10 MB in `/var/log/chatbot/` |

The script predates the `zsync` account: prefer the `/etc/crontab` lines above, with `zsync`, for the syncs.

Logs: the API writes to `/var/log/chatbot.log` (each question appears only as its 12-character id, `Question a1b2c3d4e5f6 | 36 characters | lang=it | chunks=7 | provider=claude`, never as text); sync, watchdog and backup events go to `/var/log/chatbot-sync.log`; the cron jobs above write to `/var/log/chatbot/` (`sync_inbox.log`, `purge_logs.log`, `sync_full.log`).

### Vector Store Backup

`scripts/backup_vectorstore.sh` creates a compressed archive every night at 02:00:

```
data/backups/vectorstore_20260408_020000.tar.gz
```

Keeps the last **7 days** of backups, removing older ones automatically.

**Restore:**

```sh
cd /opt/chatbot
tar -xzf data/backups/vectorstore_YYYYMMDD_HHMMSS.tar.gz -C data/
chown -R zsync:zsync data/vectorstore   # the sync account must be able to rewrite it
service chatbot restart
```

### Watchdog

`scripts/watchdog.sh` runs every minute from `/etc/crontab`:

- if `/var/run/chatbot.maintenance` exists (created by `service chatbot stop`, removed by `service chatbot start`) it does nothing
- if the daemon in `/var/run/chatbot.pid` is not running, it runs `service chatbot start`
- if it is running but `GET http://127.0.0.1:8000/health` does not return `"status":"ok"`, it kills the daemon and the uvicorn processes (`kill -9`) and starts the service again
- if a `restart` file exists in `/var/db/zautte-sync/` (left by a sync job that is not root) it removes it and runs `service chatbot restart`
- if a `reload` file exists and the API is healthy, it removes it and calls `POST /admin/reload` with `ADMIN_API_KEY` read from `/opt/chatbot/.env`

The watchdog uses port 8000 and `/var/db/zautte-sync` whatever `API_PORT` and `SYNC_CONTROL_DIR` say: keep the defaults.

Events go to `/var/log/chatbot-sync.log`. The watchdog is also what brings the API back after every sync.

---

## System Service (FreeBSD)

### Installation

```sh
# Copy the rc.d script
cp /opt/chatbot/scripts/chatbot_rcd /usr/local/etc/rc.d/chatbot
chmod +x /usr/local/etc/rc.d/chatbot
chmod +x /opt/chatbot/start.sh      # executed by the rc.d script

# Enable the service. chatbot_user is required: create the service account first
# (see "Running as an unprivileged user" below), the script does not start as root
sysrc chatbot_enable=YES chatbot_user=zautte

# Start
service chatbot start
```

The script expects the project in `/opt/chatbot` and starts after the `ollama` rc service.

### Management Commands

```sh
service chatbot start    # start
service chatbot stop     # stop
service chatbot restart  # restart
service chatbot status   # status
```

The `scripts/chatbot_rcd` script (runs as root, and starts the API as the user in `chatbot_user`, see [Running as an unprivileged user](#running-as-an-unprivileged-user)):
- does not export `/opt/chatbot/.env`: `config/settings.py` loads it with `python-dotenv` when each process starts, so a `.env` change takes effect at the next API start (`service chatbot restart`, or the watchdog restart after a sync)
- starts `/opt/chatbot/start.sh` with `daemon(8)`: daemon PID in `/var/run/chatbot.pid`, stdout/stderr to `/var/log/chatbot.log`, reopened on `SIGHUP` (`-H`) when newsyslog rotates it
- `start.sh` runs one uvicorn process (`python -m api.serve api.main:app`, one worker) listening on port `API_PORT` (8000) on the addresses of `API_HOST` (`.env`; default `127.0.0.1,::1`, local only; set `API_HOST=0.0.0.0,::` when a proxy on another host connects, and restrict the port with the firewall); scripts that look for the API process (`scripts/sync.py`, `scripts/watchdog.sh`, this rc.d script) match `api.main:app`
- `daemon` runs without `-r`: after a crash, or after a sync kills uvicorn, the watchdog restarts the service within a minute
- `stop` creates `/var/run/chatbot.maintenance` (the watchdog then leaves the service down) and `start` removes it; `status` checks the PID with `kill -0`

### Running as an unprivileged user

The API listens on the Internet, so it should not run as root: without `chatbot_user` (or with `root`) the rc script refuses to start, unless `chatbot_allow_root="YES"` is set. Create a dedicated account and tell the rc script to use it:

```sh
pw useradd zautte -d /nonexistent -s /usr/sbin/nologin -c "Zautte service"
sysrc chatbot_user=zautte

chown -R root:wheel /opt/chatbot && chmod -R go-w /opt/chatbot  # code: nobody but root can change it
chown root:zautte /opt/chatbot/.env && chmod 640 /opt/chatbot/.env   # read by the service, changed only by root
# every directory above data/ must be traversable by the account (chmod o+x)
chown root:zautte /opt/chatbot/data && chmod 1770 /opt/chatbot/data   # the account writes in it but, with the sticky bit, cannot rename or delete what root owns
find /opt/chatbot/data/ -maxdepth 1 -type f -exec chown zautte:wheel {} +
chgrp zautte /var/log/chatbot-sync.log                           # read by /crawl-history (setup_sync_user.sh below makes it zsync:zautte)
```

### Running the sync jobs without root

`scripts/setup_sync_user.sh` (run once as root, idempotent) creates the account `zsync` and the group `zdata` (both the API account and `zsync` belong to it, so both can create files in `data/`; the sticky bit keeps each from touching the other's). `zsync` owns only what the sync writes: `vectorstore`, `crawl_cache`, `documents`, `inbox`, `backups`, `/var/log/chatbot/` and the sync log. It reads `.env.sync`, which `scripts/make_sync_env.py` builds from an allowlist of the settings the jobs need (`CRAWL_*`, `OLLAMA_BASE_URL`, `OLLAMA_EMBED_MODEL`, `OLLAMA_MODEL`, `SITE_NAME`, `SITE_URL`, `MIGRATED_DOMAINS`, `INSTANCE_DIR`, `API_PORT`, `SYNC_CONTROL_DIR`), read with the application's own `.env` parser: no API, admin, SMTP or provider secret can reach it (the wrappers select it with `ZAUTTE_ENV_FILE`; summary emails are therefore sent only when the job runs as root), so a flaw in a PDF or Office parser reaches neither the API nor its secrets. Edit `.env.sync` again when `.env` changes (run the script again).

Since `zsync` can neither signal the API nor use its admin key, `scripts/sync.py` leaves an empty request file (`reload` or `restart`) in `/var/db/zautte-sync/`; the root watchdog looks for it every minute, removes it, and reloads the index (`POST /admin/reload`) or runs `service chatbot restart`. The sync lock lives in the same folder. Start the manual syncs as that account, under the same lock:

```sh
su -m zsync -c 'cd /opt/chatbot && lockf -t 0 /var/db/zautte-sync/sync.lock venv/bin/python -m scripts.sync <mode>'
```

Change the user of the sync lines in `/etc/crontab` to `zsync` and put `zsync:zautte` on `chatbot-sync.log` in the newsyslog configuration (`scripts/newsyslog-chatbot.conf`). Started as root, `scripts.sync`, `reembed`, `cleanup_index`, `inbox_indexer` and `indexer.indexer` become `zsync` first (`SYNC_USER` names another account), before `.env` is read, and use `.env.sync`: root never writes in the folders of the sync account, and the API is restarted or reloaded through the request files. An installation with no such account keeps running as root, with a warning. Take the lock as `zsync` too: `su -m zsync -c '… lockf -t 0 $LOCK …'`. The watchdog honours a `restart` request at most once every 10 minutes and a `reload` every 2, and ignores a request that is not a regular file.

What the account owns is only what the API writes: the `.env`, and the files directly in `data/` (statistics, logs of questions, feedback, users). The code, the vector store, the downloaded documents and the inbox stay owned by root and are read-only for it; the folder `data/` itself belongs to root (group `zautte`, mode 1770) and has the sticky bit, so the account cannot rename or replace the root-owned folders inside it (`vectorstore`, `crawl_cache`, `documents`, `inbox`) that the root cron jobs write to. `purge_logs` and `adduser`, when started as root, switch to the owner of the data files before touching them (`api/privdrop.py`). The `daemon(8)` supervisor stays root (it owns the pid file and the log), and the watchdog stays root. The sync jobs do not (see the previous section). Deploying code now needs root.

### Log Rotation

Copy the newsyslog configuration:

```sh
cp /opt/chatbot/scripts/newsyslog-chatbot.conf /etc/newsyslog.conf.d/chatbot.conf
```

Rotation configured:

| File                        | When                  | Kept                 | Compression |
|-----------------------------|-----------------------|----------------------|-------------|
| `/var/log/chatbot.log`      | every day at midnight | 90 files (90 days)   | bzip2 (J)   |
| `/var/log/chatbot-sync.log` | above 100 MB          | 14 files             | bzip2 (J)   |

`/var/log/chatbot.log` holds the client IP addresses (uvicorn access log), so it is kept for 90 days and then deleted (change the file count in `scripts/newsyslog-chatbot.conf` to match the retention in your privacy notice). newsyslog sends `SIGHUP` to the daemon in `/var/run/chatbot.pid`, which reopens the file; the service must have been started by the rc.d script with `-H` .

---

### Host Hardening

Measures recommended for the machine that runs Zautte (the real addresses and networks belong in your own configuration, not in the repository):

- **Firewall (pf)**: block incoming traffic except SSH and the API port from the management networks and the API port from the load balancer, and allow ping and the ICMPv6 messages IPv6 needs. Outbound traffic is allowed per account (pf matches the owner of the socket): the API account may only reach HTTPS (the model's API), SMTP submission and the resolvers; the sync account HTTP, HTTPS, SMTP submission and the resolvers; `ntpd` NTP and DNS; root and the administrator everything; the Ollama account and any other account nothing (Ollama serves local models; to download one, uncomment its rule while the download runs). A flaw in a service, or a dependency that calls home, cannot reach other hosts. Template in `scripts/pf.conf.example`; reload with `pfctl -f /etc/pf.conf` (open connections are kept) and look for `block out` in `/var/log/pflog` after a change.
- **syslogd** without a network socket (`syslogd_flags="-ss"`, UDP 514 closed).
- **sysctl** (`/etc/sysctl.conf`), as bsdinstall's hardening options: `security.bsd.unprivileged_read_msgbuf=0`, `security.bsd.unprivileged_proc_debug=0`, `security.bsd.hardlink_check_uid=1`, `security.bsd.hardlink_check_gid=1`, `kern.randompid=1` (reading it back returns the random modulus, not 1), `net.inet.tcp.blackhole=2`, `net.inet.udp.blackhole=1`, `net.inet.ip.random_id=1`, `net.inet.icmp.drop_redirect=1`, `net.inet6.icmp6.rediraccept=0`. `security.bsd.see_other_uids` stays at 1 so the API and sync processes can be checked without root.
- **NTP**: `ntpd_enable=YES`, `ntpd_sync_on_start=YES`.
- **Single-user mode** asking for the root password (`console … insecure` in `/etc/ttys`).
- **Permissions**: `data/` and the backup directory `750`, files with questions and users (`users.json`, `usage.jsonl`, `answers.jsonl`, `feedback.jsonl`, …) `640`, every copy of `.env` `600`, no old `.env` copies under the application directory.
- **API**: `/docs` and `/openapi.json` off (`API_DOCS=false`, the default).
- **Privileges and SSH**: run the service and the syncs with the least privileges that work, keep the code and the scripts that a privileged account runs owned by that account, and prefer SSH keys to passwords.

## Monitoring and Evaluation

### Health Check

```sh
curl http://127.0.0.1:8000/health                          # status, LLM provider, uptime
curl -H "X-Admin-Key: <key>" http://127.0.0.1:8000/health  # + chunks, last indexing, gaps, feedback, token costs
```

### Vector Store Statistics (admin)

```sh
curl -H "X-Admin-Key: <key>" http://127.0.0.1:8000/stats
```

### Content Gaps (admin)

```sh
curl -H "X-Admin-Key: <key>" http://127.0.0.1:8000/gaps
```

Shows the latest unanswered or weakly answered questions. Use them to identify topics to add to the site, documents to upload to the inbox, or known facts to write.

### Negative Feedback

```sh
venv/bin/python -m scripts.feedback_open             # open 👎 reports
venv/bin/python -m scripts.feedback_open --details   # only reports with a comment or links
```

The same reports are in the restricted admin area, with a "Resolve" button (`POST /feedback/resolve`).

### Deleting questions on request

When someone asks for their questions to be deleted, `scripts/forget.py` removes them from `usage.jsonl`, `answers.jsonl`, `gaps.jsonl`, `feedback.jsonl` and the "resolved" marks, and from the memory of the running API (most-frequent-questions counter saved in `stats.json`, response cache), with no restart:

```sh
cd /opt/chatbot
venv/bin/python -m scripts.forget --user name@example.org --last 1     # their last question
venv/bin/python -m scripts.forget --user name@example.org --all        # everything, feedback included
venv/bin/python -m scripts.forget --rid a1b2c3d4e5f6                    # one question, by id
```

It first lists the questions it found and asks for confirmation (`--dry-run` stops there, `--yes` skips the question). It calls `POST /usage/forget` on `http://127.0.0.1:8000` (`--api` to change it) and needs `ADMIN_API_KEY` from `.env`, so it must run as the owner of `.env` or as root. The id of an anonymous visitor's question can be read in `/var/log/chatbot.log` next to the time it was asked.

Not covered: notification emails already sent for a report, and copies kept by the model provider for questions that reached it (see [Privacy and Security](privacy-and-security.md)).

### Automated Evaluation Script

`scripts/eval.py` runs 9 test questions and measures retrieval and answer quality:

```sh
# Retrieval only (faster)
venv/bin/python -m scripts.eval --no-llm

# Retrieval + full LLM answer
venv/bin/python -m scripts.eval
```

**Metrics measured:**

| Metric               | Description                                              |
|----------------------|----------------------------------------------------------|
| Retrieval OK         | Questions for which sufficient chunks are found          |
| Keyword score        | % of expected keywords present in the answer (full mode only) |
| Average time         | Milliseconds per question (retrieval only with `--no-llm`, full answer otherwise) |

Results are saved in `data/eval_results.json`. In full mode each question goes through the normal answer path: with `claude` or `bedrock` it is a paid API call, and questions with no or weakly relevant retrieved content are logged in `gaps.jsonl`.

**Included test questions (replaceable with site-specific ones):** identity card, change of residence, school transport, access to records, library hours, waste tax, building permit, municipal police, nursery school.

---

## Troubleshooting

### Backend not responding

```sh
service chatbot status
tail -f /var/log/chatbot.log
curl http://127.0.0.1:8000/health
ls /var/run/chatbot.maintenance   # if present, the watchdog will not restart the service
```

### Empty vector store (chunks=0 in all responses)

```sh
# Check how many chunks are indexed
curl -H "X-Admin-Key: <key>" http://127.0.0.1:8000/health   # → indexed_chunks
venv/bin/python -m indexer.indexer --stats                    # chunks on disk

# If 0: run a full sync
venv/bin/python -m scripts.sync full
```

### Ollama unreachable

```sh
ollama list                      # check available models
curl http://localhost:11434/api/tags
# If Ollama is not responding:
service ollama start             # or equivalent command on FreeBSD
```

### Upgrading Ollama

The `ollama` package is locked (`pkg lock ollama`), so a general `pkg upgrade` cannot change it: a new Ollama version can compute slightly different vectors, and vectors that no longer match the index degrade search until everything is re-embedded. Upgrade it on its own:

```sh
cd /opt/chatbot
venv/bin/python -m scripts.embed_check save /root/embed_before.npz      # with the current version
pkg unlock -y ollama && pkg upgrade -y ollama
service ollama stop; while pgrep -q -f /usr/local/bin/ollama; do sleep 1; done; service ollama start
venv/bin/python -m scripts.embed_check compare /root/embed_before.npz   # exits 2 if the vectors changed
pkg lock -y ollama
```

The Ollama log (`/var/log/ollama-<user>.log`) grows with every request. newsyslog cannot rotate it, because the rc.d script starts `daemon(8)` without `-H` and the file would not be reopened; `scripts/rotate_ollama_log.sh` copies and truncates it instead (it appends, so this is safe). Run it daily from `/etc/crontab` as root: `0 4 * * * root /opt/chatbot/scripts/rotate_ollama_log.sh` (rotates above 50 MB, keeps 8 compressed copies; `OLLAMA_LOG_MAX_KB` and `OLLAMA_LOG_COUNT` change that).

Do not use `service ollama restart`: if the old process has not exited yet, `start` finds it, gives up ("process already running") and leaves Ollama down, and with it every answer. Keep a copy of the old package (`/var/cache/pkg/ollama-<version>.pkg`) to go back with `pkg unlock -y ollama && pkg delete -fy ollama && pkg add <file>`. Since 0.31 the rc.d script defaults to `ollama_user=nobody` with models in `/var/lib/ollama/models`. Do not run Ollama as root: a flaw in its model download (CVE-2026-103663, versions 0.34.2 to 0.35.0) allows writing a library that is loaded after a restart, which as root means a full takeover. Create a dedicated account (`pw useradd ollama -d /var/db/ollama -s /usr/sbin/nologin`), copy the models to `/var/db/ollama/.ollama/models`, `chown -R ollama:ollama /var/db/ollama`, set `ollama_user="ollama"` in `/etc/rc.conf`, then stop, wait for the old process to exit and start (never `restart`), and run `embed_check compare` as above.

### Vector store integrity

`data/vectorstore/` holds `embeddings.npy`, `metadata.json`, `ids.json` and `manifest.json`. A save writes the three files aside first, renames them one after the other and writes the manifest last; at load the files must match it. If they do not (a save was interrupted), or any file cannot be read, the log says `Vector store unreadable`, the API works with an empty store and **nothing overwrites the files on disk**: sync jobs fail at their first write instead of replacing the real index with an empty one. Restore the latest backup (see Backup above) and restart. A store written before the manifest existed loads without it, and gets one at its next save; `python -c "from indexer import vector_store as vs; vs.write_manifest()"` (as the sync account) creates it at once. Rows that are not numbers, or whose length is not 1, are neutralised at load and logged.

### Changing embedding model

Vectors from different models (or from a different Ollama version, which can also change them) are not comparable: if `OLLAMA_EMBED_MODEL` changes, every chunk must be re-embedded. `scripts/reembed.py` does it without crawling again: the API keeps answering with the old model while the new vectors are computed, and the switch needs only an API restart (about 15 seconds). Example with `bge-m3`, the current default:

```sh
ollama pull bge-m3
# 1. compute the new vectors in a cache, while production keeps using the old model
#    (resumable; identical texts are embedded once; --threads N limits the Ollama threads
#    to leave CPU to the API)
nice venv/bin/python -m scripts.reembed build --model bge-m3 --cache /opt/chatbot/data/reembed/bge-m3
venv/bin/python -m scripts.reembed status --cache /opt/chatbot/data/reembed/bge-m3
# 2. with no sync running (LOCK as in the manual sync above): rewrite embeddings.npy (chunks added after the build are
#    embedded now), then switch the model in .env and restart the API
lockf -t 0 $LOCK venv/bin/python -m scripts.reembed apply \
    --model bge-m3 --cache /opt/chatbot/data/reembed/bge-m3 --backup /opt/chatbot/data/backups_keep
sed -i '' 's/^OLLAMA_EMBED_MODEL=.*/OLLAMA_EMBED_MODEL=bge-m3/' .env
service chatbot restart
```

The similarity thresholds and fusion weights in `api/rag.py` (`MIN_SIMILARITY`, `RETRIEVAL_CONFIDENCE`, `VECTOR_WEIGHT`/`BM25_WEIGHT`) depend on the model and must be calibrated again. A model with a different vector size also needs `EMBEDDING_DIMENSION` changed in `config/settings.py` before `apply`. The old vectors saved by `apply --backup` allow going back only until the next sync adds or removes chunks; after that, going back means re-embedding with the old model. On a new installation, simply clear the store and run `scripts.sync full`.

### Changing chunking configuration

Chunking is set by `CHUNK_SIZE` (800) and `CHUNK_OVERLAP` (100) in `config/settings.py` and by `MAX_PARAGRAPH_CHARS` (1200) in `indexer/chunker.py`. To re-chunk the corpus already downloaded:

```sh
LOCK=/var/db/zautte-sync/sync.lock; [ -d /var/db/zautte-sync ] || LOCK=/var/run/chatbot-sync.lock
lockf -t 0 $LOCK venv/bin/python -m scripts.sync full-index
```

It does not crawl: it uses `data/crawl_cache/index.json`. It clears the store first, so chunks from `MIGRATED_DOMAINS` and inbox documents already in `data/inbox/processed/` are lost (move them back to `data/inbox/` to re-index them). Every chunk is re-embedded, which takes hours.

### Manual restart

```sh
# Restarts only the API; a running `python -m scripts.sync` is not affected
service chatbot restart
# Alternative: kill the API process with SIGKILL (it ignores SIGTERM, see
# "Shutdown"); start.sh and daemon exit, and the watchdog restarts the service
# within a minute
pkill -9 -f "api.main:app"
```

### Cron email with "Permission denied"

```sh
chmod +x /opt/chatbot/scripts/watchdog.sh
chmod +x /opt/chatbot/scripts/backup_vectorstore.sh
chmod +x /opt/chatbot/scripts/incremental_sync.sh
chmod +x /opt/chatbot/scripts/full_sync.sh
chmod +x /opt/chatbot/scripts/news_sync.sh
chmod +x /opt/chatbot/scripts/rotate_ollama_log.sh
chmod +x /opt/chatbot/scripts/setup_sync_user.sh
```

---

← [Frontend Widget](widget.md) · [Documentation index](README.md) · [Privacy and Security](privacy-and-security.md) →
