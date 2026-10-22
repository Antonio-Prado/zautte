#!/bin/sh
# Watchdog: restart the chatbot if it does not respond, and carry out the requests of the
# sync jobs (they run as an unprivileged account and cannot touch the API themselves).
# Add to /etc/crontab:
#   * * * * * root /opt/chatbot/scripts/watchdog.sh
#
# For an intentional stop without automatic restart:
#   service chatbot stop   (rc.d creates /var/run/chatbot.maintenance)
# To re-enable automatic restart:
#   service chatbot start  (rc.d removes /var/run/chatbot.maintenance)
#
# Requests: an empty file `restart` or `reload` in $CONTROL, created by scripts/sync.py.
# Only its existence is looked at; it is removed before acting.

LOG=/var/log/chatbot-sync.log
PIDFILE=/var/run/chatbot.pid
MAINTENANCE=/var/run/chatbot.maintenance
HEALTH_URL=http://127.0.0.1:8000/health
CONTROL=/var/db/zautte-sync
ENVFILE=/opt/chatbot/.env

stamp() { date '+%Y-%m-%d %H:%M:%S'; }

# If the service is in maintenance, do nothing
if [ -f "$MAINTENANCE" ]; then
    exit 0
fi

# A sync asked for a restart (new index to load): the same as restarting by hand
# The folder belongs to the sync account, which could ask over and over: a request is honoured only
# if it is a regular file and at most once in RESTART_GAP seconds (the time is kept where only root writes)
STATE=/var/run/zautte-watchdog.state
RESTART_GAP=600
RELOAD_GAP=120
now=$(date +%s)
for r in restart reload; do
    [ -L "$CONTROL/$r" ] && rm -f "$CONTROL/$r"
done
if [ -f "$CONTROL/restart" ]; then
    last=$(cat "$STATE.restart" 2>/dev/null || echo 0)
    if [ $((now - last)) -ge $RESTART_GAP ]; then
        rm -f "$CONTROL/restart" "$CONTROL/reload"
        echo "$now" > "$STATE.restart"
        echo "$(stamp) — Watchdog: restart requested by a sync" >> $LOG
        service chatbot restart </dev/null >> $LOG 2>&1
    elif [ $((now - last)) -lt 60 ]; then
        # the request stays on disk and is honoured at the first run after the gap
        echo "$(stamp) — Watchdog: restart request postponed (one every ${RESTART_GAP}s at most)" >> $LOG
    fi
    exit 0
fi

# Check whether the process is alive
if [ -f "$PIDFILE" ] && kill -0 $(cat "$PIDFILE") 2>/dev/null; then
    # Process alive — check that it responds
    STATUS=$(fetch -q -o - "$HEALTH_URL" 2>/dev/null)
    if echo "$STATUS" | grep -q '"status":"ok"'; then
        if [ -f "$CONTROL/reload" ]; then
            last=$(cat "$STATE.reload" 2>/dev/null || echo 0)
            [ $((now - last)) -ge $RELOAD_GAP ] || exit 0   # the request stays on disk until the gap has passed
            rm -f "$CONTROL/reload"
            echo "$now" > "$STATE.reload"
            KEY=$(sed -n 's/^ADMIN_API_KEY=//p' "$ENVFILE" | head -n 1 | tr -d "\"'")
            echo "$(stamp) — Watchdog: index reload requested by a sync" >> $LOG
            if [ -n "$KEY" ]; then
                ( printf 'X-Admin-Key: %s\n' "$KEY" | curl -s -m 900 -X POST -H @- \
                    http://127.0.0.1:8000/admin/reload >> $LOG 2>&1; echo >> $LOG ) </dev/null &
            fi
        fi
        exit 0  # all ok
    fi
    # Process alive but not responding. Just started (the index takes a while to load, Ollama
    # may still be loading the model): give it a few minutes before judging it stuck.
    started=$(stat -f %m "$PIDFILE" 2>/dev/null || echo 0)
    if [ $((now - started)) -lt 300 ]; then
        echo "$(stamp) — Watchdog: not answering yet, started $((now - started))s ago: waiting" >> $LOG
        exit 0
    fi
    echo "$(stamp) — Chatbot not responding, restarting..." >> $LOG
    kill -9 $(cat "$PIDFILE") 2>/dev/null
    pkill -9 -f "api.main:app" 2>/dev/null
    sleep 2
fi

# Start the service
echo "$(stamp) — Watchdog: starting chatbot" >> $LOG
service chatbot start >> $LOG 2>&1
