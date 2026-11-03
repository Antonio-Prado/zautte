#!/bin/sh
# «Amministrazione trasparente» on a Nuvola portal (scripts/amt_sync.py), every night from cron:
#   40 3 * * * zsync /opt/chatbot/scripts/amt_sync.sh >/dev/null 2>&1
# At night because the embeddings of new documents weigh on the model that also answers the users.
# Same lock as the other syncs: if one is running, the run is silently skipped.

CONTROL=/var/db/zautte-sync
if [ -d "$CONTROL" ]; then LOCKFILE=$CONTROL/sync.lock; else LOCKFILE=/var/run/chatbot-sync.lock; fi
# The unprivileged sync account reads the copy of .env without the API secrets, if there is one
if [ "$(id -u)" != 0 ] && [ -r /opt/chatbot/.env.sync ]; then export ZAUTTE_ENV_FILE=/opt/chatbot/.env.sync; fi

cd /opt/chatbot || exit 1
# Ceiling on the memory of the job (virtual size, KB), as for the other syncs (SYNC_MAX_VMEM_KB=0 lifts it)
[ "${SYNC_MAX_VMEM_KB:-25165824}" = 0 ] || ulimit -v "${SYNC_MAX_VMEM_KB:-25165824}"
# The job stops itself after "max_minutes" (default 150); this is the hard stop if it hangs
lockf -t 0 "$LOCKFILE" timeout -k 60 "${AMT_SYNC_TIMEOUT:-10800}" /opt/chatbot/venv/bin/python -m scripts.sync amt
STATUS=$?

if [ $STATUS -eq 73 ] || [ $STATUS -eq 75 ]; then
    exit 0   # another sync is running
elif [ $STATUS -eq 124 ]; then
    logger -t chatbot-amt "transparency sync stopped after ${AMT_SYNC_TIMEOUT:-10800} s"
elif [ $STATUS -ne 0 ]; then
    logger -t chatbot-amt "transparency sync failed (exit $STATUS)"
fi
