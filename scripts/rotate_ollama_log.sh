#!/bin/sh
# Rotates the Ollama logs (/var/log/ollama-<user>.log).
#
# The rc.d script of the ollama package starts daemon(8) without -H, so newsyslog's SIGHUP
# cannot make it reopen the file after a rename. daemon writes the file in append mode, so
# the log is copied and truncated in place instead.
#
# Run it from cron as root, once a day (see docs/operations.md). Keeps COUNT gzip copies.

MAX_KB=${OLLAMA_LOG_MAX_KB:-51200}
COUNT=${OLLAMA_LOG_COUNT:-8}
umask 027

for log in /var/log/ollama-*.log; do
    [ -f "$log" ] || continue
    kb=$(( $(stat -f %z "$log") / 1024 ))
    [ "$kb" -ge "$MAX_KB" ] || continue
    i=$((COUNT - 1))
    while [ "$i" -ge 1 ]; do
        [ -f "$log.$i.gz" ] && mv "$log.$i.gz" "$log.$((i + 1)).gz"
        i=$((i - 1))
    done
    rm -f "$log.$((COUNT + 1)).gz"
    gzip -c "$log" > "$log.1.gz" && : > "$log"
done
