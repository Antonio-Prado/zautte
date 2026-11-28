#!/bin/sh
# The pages the known facts come from, read again (scripts/known_facts_watch.py), once a day from cron, at an
# hour when no sync is crawling the same sites (the report of the morning after tells what it found):
#   20 14 * * * zsync /opt/chatbot/scripts/known_facts_watch.sh >>/var/log/chatbot-sync.log 2>&1
# The job reads a hundred pages or so, a few seconds apart, and writes only its own file in data/facts/,
# so it does not take the lock of the syncs. Like the other jobs it runs under a memory ceiling and a time
# limit, with its own lock.

CONTROL=/var/db/zautte-sync
if [ -d "$CONTROL" ]; then LOCKFILE=$CONTROL/known_facts_watch.lock; else LOCKFILE=/var/run/chatbot-known_facts_watch.lock; fi
# The unprivileged sync account reads the copy of .env without the API secrets, if there is one
if [ "$(id -u)" != 0 ] && [ -r /opt/chatbot/.env.sync ]; then export ZAUTTE_ENV_FILE=/opt/chatbot/.env.sync; fi

cd /opt/chatbot || exit 1
# Ceiling on the memory of the job (virtual size, KB), as for the other syncs (SYNC_MAX_VMEM_KB=0 lifts it)
[ "${SYNC_MAX_VMEM_KB:-25165824}" = 0 ] || ulimit -v "${SYNC_MAX_VMEM_KB:-25165824}"
# A normal run takes ten minutes or so (the requests are paced): stopped after KNOWN_FACTS_WATCH_TIMEOUT
# seconds; skipped if the previous one still runs
lockf -s -t 0 "$LOCKFILE" timeout -k 30 "${KNOWN_FACTS_WATCH_TIMEOUT:-2400}" /opt/chatbot/venv/bin/python -m scripts.known_facts_watch
STATUS=$?

if [ $STATUS -eq 73 ] || [ $STATUS -eq 75 ]; then
    exit 0   # the previous run is still going
elif [ $STATUS -eq 124 ]; then
    logger -t chatbot-known_facts_watch "known_facts_watch stopped after ${KNOWN_FACTS_WATCH_TIMEOUT:-2400} s"
fi
exit $STATUS
