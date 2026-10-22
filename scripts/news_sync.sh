#!/bin/sh
# Near real-time news: every 15 minutes from cron.
#   */15 * * * * zsync /opt/chatbot/scripts/news_sync.sh >/dev/null 2>&1
# Same lock as the weekly syncs: if one is running, the run is silently skipped.

CONTROL=/var/db/zautte-sync
if [ -d "$CONTROL" ]; then LOCKFILE=$CONTROL/sync.lock; else LOCKFILE=/var/run/chatbot-sync.lock; fi
# The unprivileged sync account reads the copy of .env without the API secrets, if there is one
if [ "$(id -u)" != 0 ] && [ -r /opt/chatbot/.env.sync ]; then export ZAUTTE_ENV_FILE=/opt/chatbot/.env.sync; fi

cd /opt/chatbot || exit 1
# Ceiling on the memory of the job (virtual size, KB): a hostile PDF or page that makes the
# parser blow up ends the job, not the API that shares the machine (SYNC_MAX_VMEM_KB=0 lifts it)
[ "${SYNC_MAX_VMEM_KB:-25165824}" = 0 ] || ulimit -v "${SYNC_MAX_VMEM_KB:-25165824}"
# A hung run must not hold the lock for ever and make every later sync skip: stopped after
# SYNC_TIMEOUT seconds (a normal run takes seconds; every 15 minutes is the rhythm)
lockf -t 0 "$LOCKFILE" timeout -k 60 "${SYNC_TIMEOUT:-1500}" /opt/chatbot/venv/bin/python -m scripts.sync news
STATUS=$?

if [ $STATUS -eq 73 ] || [ $STATUS -eq 75 ]; then
    exit 0   # previous sync still running
elif [ $STATUS -eq 124 ]; then
    logger -t chatbot-news "news sync stopped after ${SYNC_TIMEOUT:-1500} s"
elif [ $STATUS -ne 0 ]; then
    logger -t chatbot-news "news sync failed (exit $STATUS)"
fi
