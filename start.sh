#!/bin/sh

cd /opt/chatbot || exit 1

cleanup() {
    trap '' INT TERM EXIT
    [ -n "${pid:-}" ] && kill "${pid}" 2>/dev/null
    wait 2>/dev/null
    exit 0
}

trap cleanup INT TERM EXIT

# Un solo processo per IPv4 e IPv6 (api/serve.py): con due processi, uno per
# famiglia di indirizzi, vector store, cache e statistiche erano doppi e
# data/stats.json veniva riscritto a turno da entrambi.
/opt/chatbot/venv/bin/python -m api.serve api.main:app --host 0.0.0.0 --host :: --port 8000 &
pid=$!

# Resta vivo finché è vivo il server.
while kill -0 "${pid}" 2>/dev/null; do
    sleep 1
done

cleanup
