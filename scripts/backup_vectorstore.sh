#!/bin/sh
# Backup giornaliero del vector store.
# Aggiungere a /etc/crontab:
#   0 2 * * * root /opt/chatbot/scripts/backup_vectorstore.sh

STORE=/opt/chatbot/data/vectorstore
BACKUP_DIR=/opt/chatbot/data/backups
LOG=/var/log/chatbot-sync.log
KEEP=7  # giorni di backup da conservare

mkdir -p "$BACKUP_DIR"

DATE=$(date '+%Y%m%d_%H%M%S')
DEST="$BACKUP_DIR/vectorstore_$DATE.tar.gz"

tar -czf "$DEST" -C /opt/chatbot/data vectorstore 2>/dev/null
if [ $? -eq 0 ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') — Backup OK: $DEST" >> $LOG
else
    echo "$(date '+%Y-%m-%d %H:%M:%S') — Backup FALLITO" >> $LOG
    exit 1
fi

# Rimuovi backup più vecchi di KEEP giorni
find "$BACKUP_DIR" -name "vectorstore_*.tar.gz" -mtime +$KEEP -delete
echo "$(date '+%Y-%m-%d %H:%M:%S') — Backup vecchi rimossi (>$KEEP giorni)" >> $LOG
