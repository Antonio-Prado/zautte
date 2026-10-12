#!/bin/sh
# Notizie quasi in tempo reale: ogni 15 minuti dal cron di root.
#   */15 * * * * root /opt/chatbot/scripts/news_sync.sh >/dev/null 2>&1
# Stesso lock dei sync settimanali: se uno è in corso la corsa salta in silenzio.

LOCKFILE=/var/run/chatbot-sync.lock

cd /opt/chatbot || exit 1
lockf -t 0 "$LOCKFILE" /opt/chatbot/venv/bin/python -m scripts.sync news
STATUS=$?

if [ $STATUS -eq 73 ] || [ $STATUS -eq 75 ]; then
    exit 0   # sync precedente ancora in corso
elif [ $STATUS -ne 0 ]; then
    logger -t chatbot-news "sync notizie terminato con errore (exit $STATUS)"
fi
