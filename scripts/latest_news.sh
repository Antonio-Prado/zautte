#!/bin/sh
# The latest news (scripts/latest_news.py), every 15 minutes from cron:
#   9-59/15 * * * * zsync /opt/chatbot/scripts/latest_news.sh >>/var/log/chatbot-sync.log 2>&1
# The job reads the news bulletin (one request): like the hourly jobs it runs under a memory
# ceiling and a time limit, and with its own lock (it writes only the dynamic facts file, not the
# vector store, so it does not take the lock of the syncs).

CONTROL=/var/db/zautte-sync
if [ -d "$CONTROL" ]; then LOCKFILE=$CONTROL/latest_news.lock; else LOCKFILE=/var/run/chatbot-latest_news.lock; fi
# The unprivileged sync account reads the copy of .env without the API secrets, if there is one
if [ "$(id -u)" != 0 ] && [ -r /opt/chatbot/.env.sync ]; then export ZAUTTE_ENV_FILE=/opt/chatbot/.env.sync; fi

cd /opt/chatbot || exit 1
# Ceiling on the memory of the job (virtual size, KB), as for the other syncs (SYNC_MAX_VMEM_KB=0 lifts it)
[ "${SYNC_MAX_VMEM_KB:-25165824}" = 0 ] || ulimit -v "${SYNC_MAX_VMEM_KB:-25165824}"
# A normal run takes a few seconds: stopped after HOURLY_JOB_TIMEOUT seconds; skipped if the previous one still runs
lockf -s -t 0 "$LOCKFILE" timeout -k 30 "${HOURLY_JOB_TIMEOUT:-600}" /opt/chatbot/venv/bin/python -m scripts.latest_news
STATUS=$?

if [ $STATUS -eq 73 ] || [ $STATUS -eq 75 ]; then
    exit 0   # the previous run is still going
elif [ $STATUS -eq 124 ]; then
    logger -t chatbot-latest_news "latest_news stopped after ${HOURLY_JOB_TIMEOUT:-600} s"
fi
exit $STATUS
