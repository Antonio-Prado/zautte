"""
Production startup: a single uvicorn process listening on IPv4 and IPv6.

uvicorn from the command line opens only one address and on FreeBSD «::» does not accept
IPv4 connections (net.inet6.ip6.v6only=1), so start.sh launched two processes,
one per address family, with separate memory: each loaded its own
vector store, had its own cache and counters and took turns rewriting
data/stats.json, losing the other's counts. Here the two sockets are opened and
passed to a single server.

    python -m api.serve api.main:app --host 0.0.0.0 --host :: --port 8000

Without --host it listens locally only (127.0.0.1 and ::1): opening it to the whole
network is an explicit choice of whoever starts it (start.sh in production).

The proxy addresses to trust for X-Forwarded-For (FORWARDED_ALLOW_IPS)
are read from config.settings, i.e. also from .env: uvicorn alone only looks at
the process environment and would do so before the app loads .env.

The app appears as an argument because the commands that look for the API process
(scripts/sync.py, scripts/watchdog.sh, scripts/chatbot_rcd) use «api.main:app».
"""

import argparse
import logging
import socket

import uvicorn

from config.settings import FORWARDED_ALLOW_IPS


def _listen(host: str, port: int) -> socket.socket:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if family == socket.AF_INET6:
        # IPv6 only: IPv4 connections have their own socket (this also holds where the
        # system, like macOS and Linux, would open «::» to both families).
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
    sock.bind((host, port))
    return sock


def main() -> None:
    parser = argparse.ArgumentParser(description="Avvia l'API su più indirizzi con un solo processo")
    parser.add_argument("app", nargs="?", default="api.main:app")
    parser.add_argument("--host", action="append", help="ripetibile (default: 127.0.0.1 e ::1)")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    hosts = args.host or ["127.0.0.1", "::1"]
    sockets = [_listen(h, args.port) for h in hosts]
    # Config also configures uvicorn's logging
    config = uvicorn.Config(args.app, forwarded_allow_ips=FORWARDED_ALLOW_IPS)
    log = logging.getLogger("uvicorn.error")
    log.info("In ascolto sulla porta %d di %s", args.port, ", ".join(hosts))
    log.info("X-Forwarded-For accettato da %s", FORWARDED_ALLOW_IPS)
    uvicorn.Server(config).run(sockets=sockets)


if __name__ == "__main__":
    main()
