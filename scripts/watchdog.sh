#!/bin/sh
# Watchdog: restart the chatbot if it does not respond.
# Add to /etc/crontab:
#   * * * * * root /opt/chatbot/scripts/watchdog.sh
#
# For an intentional stop without automatic restart:
#   service chatbot stop   (rc.d creates /var/run/chatbot.maintenance)
# To re-enable automatic restart:
#   service chatbot start  (rc.d removes /var/run/chatbot.maintenance)

LOG=/var/log/chatbot-sync.log
PIDFILE=/var/run/chatbot.pid
MAINTENANCE=/var/run/chatbot.maintenance
HEALTH_URL=http://127.0.0.1:8000/health

# If the service is in maintenance, do nothing
if [ -f "$MAINTENANCE" ]; then
    exit 0
fi

# Check whether the process is alive
if [ -f "$PIDFILE" ] && kill -0 $(cat "$PIDFILE") 2>/dev/null; then
    # Process alive — check that it responds
    STATUS=$(fetch -q -o - "$HEALTH_URL" 2>/dev/null)
    if echo "$STATUS" | grep -q '"status":"ok"'; then
        exit 0  # all ok
    fi
    # Process alive but not responding — kill it
    echo "$(date '+%Y-%m-%d %H:%M:%S') — Chatbot not responding, restarting..." >> $LOG
    kill -9 $(cat "$PIDFILE") 2>/dev/null
    pkill -9 -f "api.main:app" 2>/dev/null
    sleep 2
fi

# Start the service
echo "$(date '+%Y-%m-%d %H:%M:%S') — Watchdog: starting chatbot" >> $LOG
service chatbot start >> $LOG 2>&1
