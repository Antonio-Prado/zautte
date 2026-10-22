#!/bin/sh
# LEGACY: predates the unprivileged sync account and the wrappers in this folder. The production
# schedule is the /etc/crontab in docs/operations.md (sync jobs as `zsync`, backup, purge and
# watchdog as root). This script wrote root job logs into a folder owned by the sync account,
# which on FreeBSD lets that account redirect them through a symbolic link: do not use it.
echo "cron_setup.sh is no longer supported: use the /etc/crontab lines in docs/operations.md" >&2
exit 1

CHATBOT_DIR="/opt/chatbot"
VENV_PYTHON="${CHATBOT_DIR}/venv/bin/python"
LOG_DIR="/var/log/chatbot"
CRON_USER="chatbot"

# Create the log directory
mkdir -p "${LOG_DIR}"
chown "${CRON_USER}" "${LOG_DIR}" 2>/dev/null || true

# Function that adds a line to the crontab only if it does not already exist
add_cron() {
    line="$1"
    crontab -u "${CRON_USER}" -l 2>/dev/null | grep -qF "${line}" && return
    (crontab -u "${CRON_USER}" -l 2>/dev/null; echo "${line}") \
        | crontab -u "${CRON_USER}" -
    echo "Added: ${line}"
}

echo "Configuring cron jobs for user '${CRON_USER}'..."

# --- Incremental sync every Monday at 02:30 ---
# Checks the site for changes and indexes new/updated documents.
# The wrapper takes the /var/run/chatbot-sync.lock lock (skips if already held).
add_cron "30 2 * * 1 ${CHATBOT_DIR}/scripts/incremental_sync.sh"

# --- Full sync on the 1st of every month at 03:00 ---
# Rescans the whole site (detects removed pages, updates changed content).
# Does not empty the index: the index stays searchable during the whole rebuild.
# The wrapper uses the same lock as the incremental sync (waits up to 12h).
add_cron "0 3 1 * * ${CHATBOT_DIR}/scripts/full_sync.sh"

# --- Inbox every 30 minutes during working hours (Mon-Fri 8-18) ---
# Immediately processes manually uploaded documents
add_cron "*/30 8-18 * * 1-5 cd ${CHATBOT_DIR} && ${VENV_PYTHON} -m scripts.sync inbox >> ${LOG_DIR}/sync_inbox.log 2>&1"

# --- User log retention every day at 02:15 ---
# Deletes (or strips the text from) usage/gaps/feedback entries older than the
# RETENTION_* days configured in .env (see config/settings.py).
add_cron "15 2 * * * cd ${CHATBOT_DIR} && ${VENV_PYTHON} -m scripts.purge_logs >> ${LOG_DIR}/purge_logs.log 2>&1"

# --- Weekly log rotation (Sunday at 04:00) ---
add_cron "0 4 * * 0 find ${LOG_DIR} -name '*.log' -size +10M -exec sh -c 'mv \"\$1\" \"\$1.old\" && gzip \"\$1.old\"' _ {} \;"

echo ""
echo "Cron jobs configured. Check with: crontab -u ${CRON_USER} -l"
echo ""
echo "Active schedule:"
echo "  02:30 every Monday   → incremental sync (changes)"
echo "  03:00 1st of the month → full sync (full crawl, index always live)"
echo "  every 30 min (8-18 Mon-Fri) → document inbox"
echo "  02:15 every day     → user log expiry (purge_logs)"
