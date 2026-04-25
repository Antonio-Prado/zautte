#!/bin/sh

cd /opt/chatbot || exit 1

cleanup() {
    trap '' INT TERM EXIT
    [ -n "${pid4:-}" ] && kill "${pid4}" 2>/dev/null
    [ -n "${pid6:-}" ] && kill "${pid6}" 2>/dev/null
    wait 2>/dev/null
    exit 0
}

trap cleanup INT TERM EXIT

/opt/chatbot/venv/bin/python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 &
pid4=$!

/opt/chatbot/venv/bin/python -m uvicorn api.main:app --host :: --port 8000 &
pid6=$!

# Resta vivo finché sono vivi entrambi.
while kill -0 "${pid4}" 2>/dev/null && kill -0 "${pid6}" 2>/dev/null; do
    sleep 1
done

cleanup
