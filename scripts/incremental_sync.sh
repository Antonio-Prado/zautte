#!/bin/sh
# Aggiornamento incrementale notturno del vector store.
# Il logging è gestito direttamente da sync.py (WatchedFileHandler).
# Aggiungere a /etc/crontab:
#   0 3 * * * root /opt/chatbot/scripts/incremental_sync.sh

LOCKFILE=/var/run/chatbot-sync.lock

cd /opt/chatbot
lockf -t 0 "$LOCKFILE" /opt/chatbot/venv/bin/python -m scripts.sync incremental
STATUS=$?

if [ $STATUS -eq 73 ]; then
    logger -t chatbot-sync 'sync precedente ancora in corso — skip'
elif [ $STATUS -ne 0 ]; then
    logger -t chatbot-sync "sync terminato con errore (exit $STATUS)"
fi
