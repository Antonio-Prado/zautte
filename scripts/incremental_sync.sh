#!/bin/sh
# Nightly incremental update of the vector store.
# Logging is handled directly by sync.py (WatchedFileHandler).
# Add to /etc/crontab:
#   0 3 * * * zsync /opt/chatbot/scripts/incremental_sync.sh

CONTROL=/var/db/zautte-sync
if [ -d "$CONTROL" ]; then LOCKFILE=$CONTROL/sync.lock; else LOCKFILE=/var/run/chatbot-sync.lock; fi
# The unprivileged sync account reads the copy of .env without the API secrets, if there is one
if [ "$(id -u)" != 0 ] && [ -r /opt/chatbot/.env.sync ]; then export ZAUTTE_ENV_FILE=/opt/chatbot/.env.sync; fi

cd /opt/chatbot
lockf -t 0 "$LOCKFILE" /opt/chatbot/venv/bin/python -m scripts.sync incremental
STATUS=$?

if [ $STATUS -eq 73 ] || [ $STATUS -eq 75 ]; then
    logger -t chatbot-sync 'previous sync still running — skip'
elif [ $STATUS -ne 0 ]; then
    logger -t chatbot-sync "sync failed (exit $STATUS)"
fi
