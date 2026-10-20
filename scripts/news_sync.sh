#!/bin/sh
# Near real-time news: every 15 minutes from cron.
#   */15 * * * * zsync /opt/chatbot/scripts/news_sync.sh >/dev/null 2>&1
# Same lock as the weekly syncs: if one is running, the run is silently skipped.

CONTROL=/var/db/zautte-sync
if [ -d "$CONTROL" ]; then LOCKFILE=$CONTROL/sync.lock; else LOCKFILE=/var/run/chatbot-sync.lock; fi
# The unprivileged sync account reads the copy of .env without the API secrets, if there is one
if [ "$(id -u)" != 0 ] && [ -r /opt/chatbot/.env.sync ]; then export ZAUTTE_ENV_FILE=/opt/chatbot/.env.sync; fi

cd /opt/chatbot || exit 1
lockf -t 0 "$LOCKFILE" /opt/chatbot/venv/bin/python -m scripts.sync news
STATUS=$?

if [ $STATUS -eq 73 ] || [ $STATUS -eq 75 ]; then
    exit 0   # previous sync still running
elif [ $STATUS -ne 0 ]; then
    logger -t chatbot-news "news sync failed (exit $STATUS)"
fi
