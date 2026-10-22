#!/bin/sh
# Daily vector store backup. Runs as root: the archives live in a folder the sync account
# cannot write, so a bug or a compromise of the sync cannot delete the index and its backups
# together. Add to /etc/crontab:
#   0 2 * * * root /opt/chatbot/scripts/backup_vectorstore.sh

STORE=/opt/chatbot/data/vectorstore
BACKUP_DIR=/opt/chatbot/data/backups
LOG=/var/log/chatbot-sync.log
KEEP=7  # days of backups to keep

umask 077   # the archives hold the whole index: readable by the owner only
if [ -L "$BACKUP_DIR" ]; then echo "$(date '+%Y-%m-%d %H:%M:%S') — Backup FAILED: $BACKUP_DIR is a link" >> $LOG; exit 1; fi
mkdir -p "$BACKUP_DIR"
if [ "$(id -u)" = 0 ]; then chown root:wheel "$BACKUP_DIR"; chmod 700 "$BACKUP_DIR"; fi

DATE=$(date '+%Y%m%d_%H%M%S')
DEST="$BACKUP_DIR/vectorstore_$DATE.tar.gz"

tar -czf "$DEST" -C /opt/chatbot/data vectorstore 2>/dev/null
if [ $? -eq 0 ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') — Backup OK: $DEST" >> $LOG
else
    echo "$(date '+%Y-%m-%d %H:%M:%S') — Backup FAILED" >> $LOG
    exit 1
fi

# Remove backups older than KEEP days
find "$BACKUP_DIR" -name "vectorstore_*.tar.gz" -mtime +$KEEP -delete
echo "$(date '+%Y-%m-%d %H:%M:%S') — Old backups removed (>$KEEP days)" >> $LOG
