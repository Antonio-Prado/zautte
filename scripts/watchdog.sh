#!/bin/sh
# Watchdog: riavvia il chatbot se non risponde.
# Aggiungere a /etc/crontab:
#   * * * * * root /opt/chatbot/scripts/watchdog.sh
#
# Per uno stop intenzionale senza riavvio automatico:
#   service chatbot stop   (il rc.d crea /var/run/chatbot.maintenance)
# Per riabilitare il riavvio automatico:
#   service chatbot start  (il rc.d rimuove /var/run/chatbot.maintenance)

LOG=/var/log/chatbot-sync.log
PIDFILE=/var/run/chatbot.pid
MAINTENANCE=/var/run/chatbot.maintenance
HEALTH_URL=http://127.0.0.1:8000/health

# Se il servizio è in manutenzione, non fare nulla
if [ -f "$MAINTENANCE" ]; then
    exit 0
fi

# Controlla se il processo è vivo
if [ -f "$PIDFILE" ] && kill -0 $(cat "$PIDFILE") 2>/dev/null; then
    # Processo vivo — verifica che risponda
    STATUS=$(fetch -q -o - "$HEALTH_URL" 2>/dev/null)
    if echo "$STATUS" | grep -q '"status":"ok"'; then
        exit 0  # tutto ok
    fi
    # Processo vivo ma non risponde — killalo
    echo "$(date '+%Y-%m-%d %H:%M:%S') — Chatbot non risponde, riavvio..." >> $LOG
    kill -9 $(cat "$PIDFILE") 2>/dev/null
    pkill -9 -f "uvicorn api.main" 2>/dev/null
    sleep 2
fi

# Avvia il servizio
echo "$(date '+%Y-%m-%d %H:%M:%S') — Watchdog: avvio chatbot" >> $LOG
service chatbot start >> $LOG 2>&1
