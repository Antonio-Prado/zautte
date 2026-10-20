#!/bin/sh
# Monthly full sync of the vector store.
# Uses the same lock as the incremental sync (incremental_sync.sh): without the lock the two
# syncs ran in parallel on the Monday following the 1st of the month and wrote
# the same store (3/08 and 7/09/2026). If another sync is running it waits up
# to 12 hours, then gives up and reports it on syslog.
# Line in /etc/crontab:
#   0 3 1 * * zsync /opt/chatbot/scripts/full_sync.sh

CONTROL=/var/db/zautte-sync
if [ -d "$CONTROL" ]; then LOCKFILE=$CONTROL/sync.lock; else LOCKFILE=/var/run/chatbot-sync.lock; fi
# The unprivileged sync account reads the copy of .env without the API secrets, if there is one
if [ "$(id -u)" != 0 ] && [ -r /opt/chatbot/.env.sync ]; then export ZAUTTE_ENV_FILE=/opt/chatbot/.env.sync; fi
LOGFILE=/var/log/chatbot/sync_full.log
WAIT_SECONDS=43200

cd /opt/chatbot
lockf -t "$WAIT_SECONDS" "$LOCKFILE" /opt/chatbot/venv/bin/python -m scripts.sync full >> "$LOGFILE" 2>&1
STATUS=$?

if [ $STATUS -eq 73 ] || [ $STATUS -eq 75 ]; then
    logger -t chatbot-sync 'full sync: another sync still running after 12h — skip'
elif [ $STATUS -ne 0 ]; then
    logger -t chatbot-sync "full sync failed (exit $STATUS)"
fi
