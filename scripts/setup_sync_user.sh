#!/bin/sh
# Runs the sync jobs (crawler, indexer, news) without root. Run it once as root, on FreeBSD:
#   sh /opt/chatbot/scripts/setup_sync_user.sh [service-user]
#
# Why: those jobs download and parse documents from the Internet (PDF, DOCX, HTML). A flaw in
# a parser must not give root. The jobs run as the account `zsync`, which owns only the
# folders it writes (vector store, crawl cache, documents, inbox, backups) and cannot reach
# the API, its secrets, or the users' data. Reloading or restarting the API is done by the
# root watchdog on a request file (see scripts/watchdog.sh).
#
# Idempotent. Afterwards change the crontab lines of the sync jobs and of the backup from
# `root` to `zsync` (docs/operations.md).

set -e
APP_USER=${1:-zautte}                 # account of the API (chatbot_user in rc.conf)
SYNC_USER=zsync
SHARED_GROUP=zdata                    # lets both accounts create files in data/
APP=/opt/chatbot
DATA=/data/chatbot/data
CONTROL=/var/db/zautte-sync

pw groupshow $SHARED_GROUP >/dev/null 2>&1 || pw groupadd $SHARED_GROUP
mkdir -p $CONTROL
pw usershow $SYNC_USER >/dev/null 2>&1 || pw useradd $SYNC_USER -d $CONTROL -s /usr/sbin/nologin -c "Zautte sync jobs"
pw groupmod $SHARED_GROUP -m $SYNC_USER,$APP_USER
chown $SYNC_USER:$SYNC_USER $CONTROL && chmod 755 $CONTROL

# What the sync jobs write
for d in vectorstore crawl_cache documents inbox backups; do
    [ -d "$DATA/$d" ] && chown -R $SYNC_USER:$SYNC_USER "$DATA/$d"
done
mkdir -p /var/log/chatbot && chown -R $SYNC_USER:$SYNC_USER /var/log/chatbot
touch /var/log/chatbot-sync.log && chown $SYNC_USER:$APP_USER /var/log/chatbot-sync.log && chmod 640 /var/log/chatbot-sync.log

# A copy of .env for the sync account, without the secrets of the API and the admin area
grep -v -E '^(AUTH_SECRET|ADMIN_API_KEY|ADMIN_PATH|ANTHROPIC_[A-Z_]*|AWS_[A-Z_]*|CLAUDE_API_KEY)=' $APP/.env > $APP/.env.sync
chown root:$SYNC_USER $APP/.env.sync && chmod 640 $APP/.env.sync

# Last, so that the API (restart it afterwards: it must pick up the new group) is not left
# without write access in data/ during the long chown above.
# data/: root's, shared group, sticky bit: each account can only rename or delete what it owns
chown root:$SHARED_GROUP $DATA && chmod 1770 $DATA

echo "done. Now: sed the sync lines of /etc/crontab from root to $SYNC_USER, and set"
echo "'root:$SYNC_USER' -> '$SYNC_USER:$APP_USER' for chatbot-sync.log in /etc/newsyslog.conf.d/chatbot.conf."
