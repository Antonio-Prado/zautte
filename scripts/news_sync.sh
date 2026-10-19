#!/bin/sh
# Near real-time news: every 15 minutes from root cron.
#   */15 * * * * root /opt/chatbot/scripts/news_sync.sh >/dev/null 2>&1
# Same lock as the weekly syncs: if one is running, the run is silently skipped.

LOCKFILE=/var/run/chatbot-sync.lock

cd /opt/chatbot || exit 1
lockf -t 0 "$LOCKFILE" /opt/chatbot/venv/bin/python -m scripts.sync news
STATUS=$?

if [ $STATUS -eq 73 ] || [ $STATUS -eq 75 ]; then
    exit 0   # previous sync still running
elif [ $STATUS -ne 0 ]; then
    logger -t chatbot-news "news sync failed (exit $STATUS)"
fi
