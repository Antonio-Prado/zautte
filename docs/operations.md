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
```

Every mode ends by killing the `uvicorn api.main:app` processes so that the API reloads the vector store; the watchdog starts the service again within a minute. The sync log is `/var/log/chatbot-sync.log`.

Only the cron wrappers (`incremental_sync.sh`, `full_sync.sh`) take the lock `/var/run/chatbot-sync.lock`. Two syncs writing the store at the same time can corrupt it, so run manual syncs under the same lock:

```sh
lockf -t 0 /var/run/chatbot-sync.lock venv/bin/python -m scripts.sync <mode>
```

During long runs the vector store is saved to disk every 30 minutes (checkpoint) and at the end.

### `/etc/crontab` (root) — production schedule

The sync wrappers need root (lock in `/var/run`, root-owned log, and every sync kills the root-owned uvicorn processes), so in production they run from `/etc/crontab`:

```
# minute hour mday month wday who  command
*        *    *    *     *    root /opt/chatbot/scripts/watchdog.sh
0        2    *    *     *    root /opt/chatbot/scripts/backup_vectorstore.sh
30       2    *    *     1    root /opt/chatbot/scripts/incremental_sync.sh >/dev/null 2>&1
0        3    1    *     *    root /opt/chatbot/scripts/full_sync.sh
```

| Schedule                       | Script                  | Description |
|--------------------------------|-------------------------|-------------|
| every minute                   | `watchdog.sh`           | Restarts the API if it is down or unresponsive |
| 02:00 every night              | `backup_vectorstore.sh` | Compressed vector store backup |
| 02:30 every Monday             | `incremental_sync.sh`   | Incremental sync; skipped if another sync holds the lock |
| 03:00 on the 1st of each month | `full_sync.sh`          | Full sync; waits up to 12 hours for the lock, output in `/var/log/chatbot/sync_full.log` |

`sync.py` also prints its log to stdout: without `>/dev/null`, cron tries to mail it and, with no local mailer, the job ends with exit 120.

### `scripts/cron_setup.sh`

The script (run it as root: `crontab -u` requires it) adds to the crontab of the user `chatbot` the incremental and full sync above, plus:

| Schedule                          | Command      | Description |
|-----------------------------------|--------------|-------------|
| every 30 min, 08:00–18:30 Mon–Fri | `sync inbox` | Processes inbox documents (no lock). As user `chatbot` it cannot kill the root-owned uvicorn processes, so the new documents are served only after the next API restart |
| 02:15 every day                   | `purge_logs` | Applies the log retention periods |
| 04:00 every Sunday                | `find … -size +10M` | Compresses logs larger than 10 MB in `/var/log/chatbot/` |

Since the sync wrappers need root (see above), prefer `/etc/crontab` for them.

Logs: the API writes to `/var/log/chatbot.log` (each question appears only as its 12-character id, `Domanda a1b2c3d4e5f6 | 36 caratteri | …`, never as text); sync, watchdog and backup events go to `/var/log/chatbot-sync.log`; the cron jobs above write to `/var/log/chatbot/` (`sync_inbox.log`, `purge_logs.log`, `sync_full.log`).

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
service chatbot restart
```

### Watchdog

`scripts/watchdog.sh` runs every minute from `/etc/crontab`:

- if `/var/run/chatbot.maintenance` exists (created by `service chatbot stop`, removed by `service chatbot start`) it does nothing
- if the daemon in `/var/run/chatbot.pid` is not running, it runs `service chatbot start`
- if it is running but `GET http://127.0.0.1:8000/health` does not return `"status":"ok"`, it kills the daemon and the uvicorn processes (`kill -9`) and starts the service again

Events go to `/var/log/chatbot-sync.log`. The watchdog is also what brings the API back after every sync.

---

## System Service (FreeBSD)

### Installation

```sh
# Copy the rc.d script
cp /opt/chatbot/scripts/chatbot_rcd /usr/local/etc/rc.d/chatbot
chmod +x /usr/local/etc/rc.d/chatbot
chmod +x /opt/chatbot/start.sh      # executed by the rc.d script

# Enable the service
echo 'chatbot_enable="YES"' >> /etc/rc.conf

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

The `scripts/chatbot_rcd` script (runs as root):
- does not export `/opt/chatbot/.env`: `config/settings.py` loads it with `python-dotenv` when each process starts, so a `.env` change takes effect at the next API start (`service chatbot restart`, or the watchdog restart after a sync)
- starts `/opt/chatbot/start.sh` with `daemon(8)`: daemon PID in `/var/run/chatbot.pid`, stdout/stderr to `/var/log/chatbot.log`, reopened on `SIGHUP` (`-H`) when newsyslog rotates it
- `start.sh` runs one uvicorn process (`python -m api.serve api.main:app`, one worker) listening on port 8000 on both IPv4 `0.0.0.0` and IPv6 `::`; scripts that look for the API process (`scripts/sync.py`, `scripts/watchdog.sh`, this rc.d script) match `api.main:app`
- `daemon` runs without `-r`: after a crash, or after a sync kills uvicorn, the watchdog restarts the service within a minute
- `stop` creates `/var/run/chatbot.maintenance` (the watchdog then leaves the service down) and `start` removes it; `status` checks the PID with `kill -0`

### Log Rotation

Copy the newsyslog configuration:

```sh
cp /opt/chatbot/scripts/newsyslog-chatbot.conf /etc/newsyslog.conf.d/chatbot.conf
```

Rotation configured:

| File                        | When                  | Kept                 | Compression |
|-----------------------------|-----------------------|----------------------|-------------|
| `/var/log/chatbot.log`      | every day at midnight | 365 files (one year) | bzip2 (J)   |
| `/var/log/chatbot-sync.log` | above 100 MB          | 14 files             | bzip2 (J)   |

`/var/log/chatbot.log` holds the client IP addresses (uvicorn access log), so it is kept for one year and then deleted. newsyslog sends `SIGHUP` to the daemon in `/var/run/chatbot.pid`, which reopens the file; the service must have been started by the rc.d script with `-H` (since 6 October 2026).

The file also lists `/var/log/chatbot-crawler.log` and `/var/log/chatbot-indexer.log`, which the current code no longer writes.

---

### Host Hardening

Applied to the production machine on 6 October 2026:

- **Firewall (pf)**: incoming traffic is blocked except SSH and port 8000 from the management networks, port 8000 from the load balancer, ping and the ICMPv6 messages IPv6 needs; outgoing traffic is open. Template in `scripts/pf.conf.example` (the real networks and load balancer address are only in `/etc/pf.conf` on the server). Blocked packets go to `/var/log/pflog` (`pflog_enable=YES`; rotation in `/etc/newsyslog.conf.d/pf.conf`): `tcpdump -n -e -ttt -r /var/log/pflog`. Enabling pf over SSH drops the connections already open, so turn it on from a detached script with a timer that runs `pfctl -d` after a few minutes unless a new SSH connection confirms that access still works. If the load balancer starts connecting from another address, add it to `/etc/pf.conf` (`pfctl -f /etc/pf.conf`) as well as to `FORWARDED_ALLOW_IPS`.
- **syslogd** without a network socket (`syslogd_flags="-ss"`, UDP 514 closed).
- **sysctl** (`/etc/sysctl.conf`), as bsdinstall's hardening options: `security.bsd.unprivileged_read_msgbuf=0`, `security.bsd.unprivileged_proc_debug=0`, `security.bsd.hardlink_check_uid=1`, `security.bsd.hardlink_check_gid=1`, `kern.randompid=1` (reading it back returns the random modulus, not 1), `net.inet.tcp.blackhole=2`, `net.inet.udp.blackhole=1`, `net.inet.ip.random_id=1`, `net.inet.icmp.drop_redirect=1`, `net.inet6.icmp6.rediraccept=0`. `security.bsd.see_other_uids` stays at 1 so the API and sync processes can be checked without root.
- **NTP**: `ntpd_enable=YES`, `ntpd_sync_on_start=YES`.
- **Single-user mode** asks for the root password (`console … insecure` in `/etc/ttys`).
- **Permissions**: `data/` and the backup directory `750`, log files with questions and users (`users.json`, `usage.jsonl`, `feedback.jsonl`, …) `640`, every copy of `.env` `600`; old `.env` copies moved out of `/opt/chatbot`.
- **API**: `/docs` and `/openapi.json` are off (`API_DOCS=false`, the default).

Still open: the API, the syncs and Ollama run as root, and the code and the scripts that root runs belong to the deploy user; SSH accepts passwords (keyboard-interactive through PAM).

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

The same reports are in the admin view of the dashboard, with a "Resolve" button (`POST /feedback/resolve`).

### Deleting questions on request

When someone asks for their questions to be deleted, `scripts/forget.py` removes them from `usage.jsonl`, `gaps.jsonl`, `feedback.jsonl` and the "resolved" marks, and from the memory of the running API (most-frequent-questions counter saved in `stats.json`, response cache), with no restart:

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

Do not use `service ollama restart`: if the old process has not exited yet, `start` finds it, gives up ("process already running") and leaves Ollama down, and with it every answer. Keep a copy of the old package (`/var/cache/pkg/ollama-<version>.pkg`) to go back with `pkg unlock -y ollama && pkg delete -fy ollama && pkg add <file>`. Since 0.31 the rc.d script defaults to `ollama_user=nobody` with models in `/var/lib/ollama/models`; production sets `ollama_user="root"` in `/etc/rc.conf`, so the models stay in `/root/.ollama`. On 6 October 2026, 0.19.0 → 0.31.1 with `bge-m3`: minimum similarity 0.999906, retrieval evaluation unchanged (hit@7 79/100, MRR@7 0.663), no re-embedding needed.

### Changing embedding model

Vectors from different models (or from a different Ollama version, which can also change them) are not comparable: if `OLLAMA_EMBED_MODEL` changes, every chunk must be re-embedded. `scripts/reembed.py` does it without crawling again: the API keeps answering with the old model while the new vectors are computed, and the switch needs only an API restart (about 15 seconds). Example with `bge-m3`, the current default (it replaced `mxbai-embed-large` in October 2026):

```sh
ollama pull bge-m3
# 1. compute the new vectors in a cache, while production keeps using the old model
#    (resumable; identical texts are embedded once; --threads N limits the Ollama threads
#    to leave CPU to the API)
nice venv/bin/python -m scripts.reembed build --model bge-m3 --cache /data/chatbot/reembed/bge-m3
venv/bin/python -m scripts.reembed status --cache /data/chatbot/reembed/bge-m3
# 2. with no sync running: rewrite embeddings.npy (chunks added after the build are
#    embedded now), then switch the model in .env and restart the API
lockf -t 0 /var/run/chatbot-sync.lock venv/bin/python -m scripts.reembed apply \
    --model bge-m3 --cache /data/chatbot/reembed/bge-m3 --backup /data/chatbot/backups_keep
sed -i '' 's/^OLLAMA_EMBED_MODEL=.*/OLLAMA_EMBED_MODEL=bge-m3/' .env
service chatbot restart
```

The similarity thresholds and fusion weights in `api/rag.py` (`MIN_SIMILARITY`, `RETRIEVAL_CONFIDENCE`, `VECTOR_WEIGHT`/`BM25_WEIGHT`) depend on the model and must be calibrated again. A model with a different vector size also needs `EMBEDDING_DIMENSION` changed in `config/settings.py` before `apply`. The old vectors saved by `apply --backup` allow going back only until the next sync adds or removes chunks; after that, going back means re-embedding with the old model. On a new installation, simply clear the store and run `scripts.sync full`.

### Changing chunking configuration

Chunking is set by `MAX_PARAGRAPH_CHARS` in `indexer/chunker.py` and `CHUNK_OVERLAP` (minimum 150) in `config/settings.py`. To re-chunk the corpus already downloaded:

```sh
lockf -t 0 /var/run/chatbot-sync.lock venv/bin/python -m scripts.sync full-index
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
```

---

← [Frontend Widget](widget.md) · [Documentation index](README.md) · [Privacy and Security](privacy-and-security.md) →
