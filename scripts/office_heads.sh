#!/bin/sh
# Who heads each office (scripts/office_heads.py), once a day from cron:
#   37 13 * * * zsync /opt/chatbot/scripts/office_heads.sh >>/var/log/chatbot-sync.log 2>&1
# The job reads the pages of the site that list the offices and the page of each one (some tens of
# requests, a few seconds apart): like the hourly jobs it runs under a memory ceiling and a time
# limit, and with its own lock (it writes only the dynamic facts file, not the vector store, so it
# does not take the lock of the syncs).

CONTROL=/var/db/zautte-sync
if [ -d "$CONTROL" ]; then LOCKFILE=$CONTROL/office_heads.lock; else LOCKFILE=/var/run/chatbot-office_heads.lock; fi
# The unprivileged sync account reads the copy of .env without the API secrets, if there is one
if [ "$(id -u)" != 0 ] && [ -r /opt/chatbot/.env.sync ]; then export ZAUTTE_ENV_FILE=/opt/chatbot/.env.sync; fi

cd /opt/chatbot || exit 1
# Ceiling on the memory of the job (virtual size, KB), as for the other syncs (SYNC_MAX_VMEM_KB=0 lifts it)
[ "${SYNC_MAX_VMEM_KB:-25165824}" = 0 ] || ulimit -v "${SYNC_MAX_VMEM_KB:-25165824}"
# A normal run takes a few minutes (the requests are paced): stopped after OFFICE_HEADS_TIMEOUT seconds;
# skipped if the previous one still runs
lockf -s -t 0 "$LOCKFILE" timeout -k 30 "${OFFICE_HEADS_TIMEOUT:-1200}" /opt/chatbot/venv/bin/python -m scripts.office_heads
STATUS=$?

if [ $STATUS -eq 73 ] || [ $STATUS -eq 75 ]; then
    exit 0   # the previous run is still going
elif [ $STATUS -eq 124 ]; then
    logger -t chatbot-office_heads "office_heads stopped after ${OFFICE_HEADS_TIMEOUT:-1200} s"
fi
exit $STATUS
