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
if [ -f "$CONTROL/restart" ]; then
    rm -f "$CONTROL/restart" "$CONTROL/reload"
    echo "$(stamp) — Watchdog: restart requested by a sync" >> $LOG
    service chatbot restart </dev/null >> $LOG 2>&1
    exit 0
fi

# Check whether the process is alive
if [ -f "$PIDFILE" ] && kill -0 $(cat "$PIDFILE") 2>/dev/null; then
    # Process alive — check that it responds
    STATUS=$(fetch -q -o - "$HEALTH_URL" 2>/dev/null)
    if echo "$STATUS" | grep -q '"status":"ok"'; then
        if [ -f "$CONTROL/reload" ]; then
            rm -f "$CONTROL/reload"
            KEY=$(sed -n 's/^ADMIN_API_KEY=//p' "$ENVFILE" | head -n 1 | tr -d "\"'")
            echo "$(stamp) — Watchdog: index reload requested by a sync" >> $LOG
            if [ -n "$KEY" ]; then
                ( printf 'X-Admin-Key: %s\n' "$KEY" | curl -s -m 900 -X POST -H @- \
                    http://127.0.0.1:8000/admin/reload >> $LOG 2>&1; echo >> $LOG ) </dev/null &
            fi
        fi
        exit 0  # all ok
    fi
    # Process alive but not responding — kill it
    echo "$(stamp) — Chatbot not responding, restarting..." >> $LOG
    kill -9 $(cat "$PIDFILE") 2>/dev/null
    pkill -9 -f "api.main:app" 2>/dev/null
    sleep 2
fi

# Start the service
echo "$(stamp) — Watchdog: starting chatbot" >> $LOG
service chatbot start >> $LOG 2>&1
