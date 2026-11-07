#!/bin/sh
# Figures of the transparency portal as known facts (scripts/transparency_facts.py), once a day from cron:
#   42 5 * * * zsync /opt/chatbot/scripts/transparency_facts.sh >>/var/log/chatbot-sync.log 2>&1
# The job reads four pages of the transparency portal and their attachments: like the hourly jobs it runs under a
# memory ceiling and a time limit, and with its own lock (it writes only the dynamic facts file,
# not the vector store, so it does not take the lock of the syncs).

CONTROL=/var/db/zautte-sync
if [ -d "$CONTROL" ]; then LOCKFILE=$CONTROL/transparency_facts.lock; else LOCKFILE=/var/run/chatbot-transparency_facts.lock; fi
# The unprivileged sync account reads the copy of .env without the API secrets, if there is one
if [ "$(id -u)" != 0 ] && [ -r /opt/chatbot/.env.sync ]; then export ZAUTTE_ENV_FILE=/opt/chatbot/.env.sync; fi

cd /opt/chatbot || exit 1
# Ceiling on the memory of the job (virtual size, KB), as for the other syncs (SYNC_MAX_VMEM_KB=0 lifts it)
[ "${SYNC_MAX_VMEM_KB:-25165824}" = 0 ] || ulimit -v "${SYNC_MAX_VMEM_KB:-25165824}"
# A normal run takes a couple of minutes: stopped after HOURLY_JOB_TIMEOUT seconds; skipped if the previous one still runs
lockf -s -t 0 "$LOCKFILE" timeout -k 30 "${HOURLY_JOB_TIMEOUT:-600}" /opt/chatbot/venv/bin/python -m scripts.transparency_facts
STATUS=$?

if [ $STATUS -eq 73 ] || [ $STATUS -eq 75 ]; then
    exit 0   # the previous run is still going
elif [ $STATUS -eq 124 ]; then
    logger -t chatbot-transparency_facts "transparency_facts stopped after ${HOURLY_JOB_TIMEOUT:-600} s"
fi
exit $STATUS
