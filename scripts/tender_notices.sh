#!/bin/sh
# Tenders open today (scripts/tender_notices.py), every hour from cron:
#   22 * * * * zsync /opt/chatbot/scripts/tender_notices.sh >>/var/log/chatbot-sync.log 2>&1
# The job reads a page or two of the transparency portal: like the other hourly jobs it runs under a
# memory ceiling and a time limit, and with its own lock (it writes only the dynamic facts file,
# not the vector store, so it does not take the lock of the syncs).

CONTROL=/var/db/zautte-sync
if [ -d "$CONTROL" ]; then LOCKFILE=$CONTROL/tender_notices.lock; else LOCKFILE=/var/run/chatbot-tender_notices.lock; fi
# The unprivileged sync account reads the copy of .env without the API secrets, if there is one
if [ "$(id -u)" != 0 ] && [ -r /opt/chatbot/.env.sync ]; then export ZAUTTE_ENV_FILE=/opt/chatbot/.env.sync; fi

cd /opt/chatbot || exit 1
# Ceiling on the memory of the job (virtual size, KB), as for the other syncs (SYNC_MAX_VMEM_KB=0 lifts it)
[ "${SYNC_MAX_VMEM_KB:-25165824}" = 0 ] || ulimit -v "${SYNC_MAX_VMEM_KB:-25165824}"
# A normal run takes under a minute: stopped after HOURLY_JOB_TIMEOUT seconds; skipped if the previous one still runs
lockf -s -t 0 "$LOCKFILE" timeout -k 30 "${HOURLY_JOB_TIMEOUT:-600}" /opt/chatbot/venv/bin/python -m scripts.tender_notices
STATUS=$?

if [ $STATUS -eq 73 ] || [ $STATUS -eq 75 ]; then
    exit 0   # the previous run is still going
elif [ $STATUS -eq 124 ]; then
    logger -t chatbot-tender_notices "tender_notices stopped after ${HOURLY_JOB_TIMEOUT:-600} s"
fi
exit $STATUS
