#!/bin/sh
# Registers of acts (scripts/acts_sync.py), twice a day from cron:
#   50 7,14 * * * zsync /opt/chatbot/scripts/acts_sync.sh >/dev/null 2>&1
# Same lock as the other syncs: if one is running, the run is silently skipped.

CONTROL=/var/db/zautte-sync
if [ -d "$CONTROL" ]; then LOCKFILE=$CONTROL/sync.lock; else LOCKFILE=/var/run/chatbot-sync.lock; fi
# The unprivileged sync account reads the copy of .env without the API secrets, if there is one
if [ "$(id -u)" != 0 ] && [ -r /opt/chatbot/.env.sync ]; then export ZAUTTE_ENV_FILE=/opt/chatbot/.env.sync; fi

cd /opt/chatbot || exit 1
# Ceiling on the memory of the job (virtual size, KB), as for the other syncs (SYNC_MAX_VMEM_KB=0 lifts it)
[ "${SYNC_MAX_VMEM_KB:-25165824}" = 0 ] || ulimit -v "${SYNC_MAX_VMEM_KB:-25165824}"
# A hung run must not hold the lock for ever: stopped after SYNC_TIMEOUT seconds (a normal run takes a minute)
lockf -t 0 "$LOCKFILE" timeout -k 60 "${SYNC_TIMEOUT:-1800}" /opt/chatbot/venv/bin/python -m scripts.sync acts
STATUS=$?

if [ $STATUS -eq 73 ] || [ $STATUS -eq 75 ]; then
    exit 0   # another sync is running
elif [ $STATUS -eq 124 ]; then
    logger -t chatbot-acts "acts sync stopped after ${SYNC_TIMEOUT:-1800} s"
elif [ $STATUS -ne 0 ]; then
    logger -t chatbot-acts "acts sync failed (exit $STATUS)"
fi
