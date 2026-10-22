#!/bin/sh
# Nightly incremental update of the vector store.
# Logging is handled directly by sync.py (WatchedFileHandler).
# Add to /etc/crontab:
#   0 3 * * * zsync /opt/chatbot/scripts/incremental_sync.sh

CONTROL=/var/db/zautte-sync
if [ -d "$CONTROL" ]; then LOCKFILE=$CONTROL/sync.lock; else LOCKFILE=/var/run/chatbot-sync.lock; fi
# The unprivileged sync account reads the copy of .env without the API secrets, if there is one
if [ "$(id -u)" != 0 ] && [ -r /opt/chatbot/.env.sync ]; then export ZAUTTE_ENV_FILE=/opt/chatbot/.env.sync; fi

cd /opt/chatbot || exit 1
# Ceiling on the memory of the job (virtual size, KB): a hostile PDF or page that makes the
# parser blow up ends the job, not the API that shares the machine (SYNC_MAX_VMEM_KB=0 lifts it)
[ "${SYNC_MAX_VMEM_KB:-25165824}" = 0 ] || ulimit -v "${SYNC_MAX_VMEM_KB:-25165824}"
# Stopped after SYNC_TIMEOUT seconds (default 16 h; a normal run takes about 5): a hung run would hold the lock
lockf -t 0 "$LOCKFILE" timeout -k 120 "${SYNC_TIMEOUT:-57600}" /opt/chatbot/venv/bin/python -m scripts.sync incremental
STATUS=$?

if [ $STATUS -eq 73 ] || [ $STATUS -eq 75 ]; then
    logger -t chatbot-sync 'previous sync still running — skip'
elif [ $STATUS -ne 0 ]; then
    logger -t chatbot-sync "sync failed (exit $STATUS)"
fi
