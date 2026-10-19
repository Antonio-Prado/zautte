#!/bin/sh
# Nightly incremental update of the vector store.
# Logging is handled directly by sync.py (WatchedFileHandler).
# Add to /etc/crontab:
#   0 3 * * * root /opt/chatbot/scripts/incremental_sync.sh

LOCKFILE=/var/run/chatbot-sync.lock

cd /opt/chatbot
lockf -t 0 "$LOCKFILE" /opt/chatbot/venv/bin/python -m scripts.sync incremental
STATUS=$?

if [ $STATUS -eq 73 ] || [ $STATUS -eq 75 ]; then
    logger -t chatbot-sync 'previous sync still running — skip'
elif [ $STATUS -ne 0 ]; then
    logger -t chatbot-sync "sync failed (exit $STATUS)"
fi
