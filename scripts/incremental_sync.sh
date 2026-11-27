#!/bin/sh
# Weekly incremental update of the vector store.
# Logging is handled directly by sync.py (WatchedFileHandler).
# Line in /etc/crontab (not at a minute when news_sync.sh starts, every 15):
#   37 2 * * 1 zsync /opt/chatbot/scripts/incremental_sync.sh >/dev/null 2>&1

CONTROL=/var/db/zautte-sync
if [ -d "$CONTROL" ]; then LOCKFILE=$CONTROL/sync.lock; else LOCKFILE=/var/run/chatbot-sync.lock; fi
# The unprivileged sync account reads the copy of .env without the API secrets, if there is one
if [ "$(id -u)" != 0 ] && [ -r /opt/chatbot/.env.sync ]; then export ZAUTTE_ENV_FILE=/opt/chatbot/.env.sync; fi

cd /opt/chatbot || exit 1
# Ceiling on the memory of the job (virtual size, KB): a hostile PDF or page that makes the
# parser blow up ends the job, not the API that shares the machine (SYNC_MAX_VMEM_KB=0 lifts it)
[ "${SYNC_MAX_VMEM_KB:-25165824}" = 0 ] || ulimit -v "${SYNC_MAX_VMEM_KB:-25165824}"
# If another sync holds the lock it waits up to SYNC_LOCK_WAIT seconds (default 3 h), then gives up and
# reports it on syslog. It used to give up at once: started in the same minute as news_sync.sh, which took
# the lock first, it was skipped every week and the pages of the site were not read again.
WAIT_SECONDS="${SYNC_LOCK_WAIT:-10800}"
# Stopped after SYNC_TIMEOUT seconds (default 16 h; a normal run takes about 5): a hung run would hold the lock
lockf -t "$WAIT_SECONDS" "$LOCKFILE" timeout -k 120 "${SYNC_TIMEOUT:-57600}" /opt/chatbot/venv/bin/python -m scripts.sync incremental
STATUS=$?

if [ $STATUS -eq 73 ] || [ $STATUS -eq 75 ]; then
    logger -t chatbot-sync "incremental sync: another sync still running after ${WAIT_SECONDS} s — skip"
elif [ $STATUS -ne 0 ]; then
    logger -t chatbot-sync "sync failed (exit $STATUS)"
fi
