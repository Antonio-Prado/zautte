#!/bin/sh
# How the council voted in its latest session (scripts/council_votes.py), once a day from cron, after the
# nightly sync of the transparency portal:
#   25 7 * * * zsync /opt/chatbot/scripts/council_votes.sh >>/var/log/chatbot-sync.log 2>&1
# The job downloads nothing: it reads the metadata of the index and writes only the dynamic facts file, so
# it does not take the lock of the syncs. Like the other jobs it runs under a memory ceiling and a time
# limit, with its own lock.

CONTROL=/var/db/zautte-sync
if [ -d "$CONTROL" ]; then LOCKFILE=$CONTROL/council_votes.lock; else LOCKFILE=/var/run/chatbot-council_votes.lock; fi
# The unprivileged sync account reads the copy of .env without the API secrets, if there is one
if [ "$(id -u)" != 0 ] && [ -r /opt/chatbot/.env.sync ]; then export ZAUTTE_ENV_FILE=/opt/chatbot/.env.sync; fi

cd /opt/chatbot || exit 1
# Ceiling on the memory of the job (virtual size, KB), as for the other syncs (SYNC_MAX_VMEM_KB=0 lifts it)
[ "${SYNC_MAX_VMEM_KB:-25165824}" = 0 ] || ulimit -v "${SYNC_MAX_VMEM_KB:-25165824}"
# A normal run takes a minute or two (the metadata of the index is one big file): stopped after
# COUNCIL_VOTES_TIMEOUT seconds;
# skipped if the previous one still runs
lockf -s -t 0 "$LOCKFILE" timeout -k 30 "${COUNCIL_VOTES_TIMEOUT:-1200}" /opt/chatbot/venv/bin/python -m scripts.council_votes
STATUS=$?

if [ $STATUS -eq 73 ] || [ $STATUS -eq 75 ]; then
    exit 0   # the previous run is still going
elif [ $STATUS -eq 124 ]; then
    logger -t chatbot-council_votes "council_votes stopped after ${COUNCIL_VOTES_TIMEOUT:-1200} s"
fi
exit $STATUS
