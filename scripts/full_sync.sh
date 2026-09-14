#!/bin/sh
# Sync completo mensile del vector store.
# Usa lo stesso lock dell'incrementale (incremental_sync.sh): senza lock i due
# sync partivano in parallelo il lunedì successivo al 1° del mese e scrivevano
# lo stesso store (3/08 e 7/09/2026). Se un altro sync è in corso aspetta fino
# a 12 ore, poi rinuncia e lo segnala su syslog.
# Riga in /etc/crontab:
#   0 3 1 * * root /opt/chatbot/scripts/full_sync.sh

LOCKFILE=/var/run/chatbot-sync.lock
LOGFILE=/var/log/chatbot/sync_full.log
WAIT_SECONDS=43200

cd /opt/chatbot
lockf -t "$WAIT_SECONDS" "$LOCKFILE" /opt/chatbot/venv/bin/python -m scripts.sync full >> "$LOGFILE" 2>&1
STATUS=$?

if [ $STATUS -eq 73 ] || [ $STATUS -eq 75 ]; then
    logger -t chatbot-sync 'full sync: altro sync ancora in corso dopo 12h — skip'
elif [ $STATUS -ne 0 ]; then
    logger -t chatbot-sync "full sync terminato con errore (exit $STATUS)"
fi
