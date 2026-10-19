#!/bin/sh

cd /opt/chatbot || exit 1

cleanup() {
    trap '' INT TERM EXIT
    [ -n "${pid:-}" ] && kill "${pid}" 2>/dev/null
    wait 2>/dev/null
    exit 0
}

trap cleanup INT TERM EXIT

# A single process for IPv4 and IPv6 (api/serve.py): with two processes, one per
# address family, vector store, cache and statistics were duplicated and
# data/stats.json was rewritten in turn by both.
/opt/chatbot/venv/bin/python -m api.serve api.main:app --host 0.0.0.0 --host :: --port 8000 &
pid=$!

# Stays alive as long as the server is alive.
while kill -0 "${pid}" 2>/dev/null; do
    sleep 1
done

cleanup
