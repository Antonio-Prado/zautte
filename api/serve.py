"""
Production startup: a single uvicorn process listening on IPv4 and IPv6.

uvicorn from the command line opens only one address and on FreeBSD «::» does not accept
IPv4 connections (net.inet6.ip6.v6only=1), so start.sh launched two processes,
one per address family, with separate memory: each loaded its own
vector store, had its own cache and counters and took turns rewriting
data/stats.json, losing the other's counts. Here the two sockets are opened and
passed to a single server.

    python -m api.serve api.main:app --host 0.0.0.0 --host :: --port 8000

Without --host it listens on API_HOST (a comma-separated list, from .env; default 127.0.0.1
and ::1, local only): opening it to the whole network is an explicit choice, made in .env
(API_HOST=0.0.0.0,::) and restricted by the firewall to the proxy and the admin networks.

The proxy addresses to trust for X-Forwarded-For (FORWARDED_ALLOW_IPS)
are read from config.settings, i.e. also from .env: uvicorn alone only looks at
the process environment and would do so before the app loads .env.

The app appears as an argument because the commands that look for the API process
(scripts/sync.py, scripts/watchdog.sh, scripts/chatbot_rcd) use «api.main:app».
"""

import argparse
import logging
import os
import socket

import uvicorn

from config.settings import API_HOST, API_PORT, FORWARDED_ALLOW_IPS


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
    parser = argparse.ArgumentParser(description="Start the API on multiple addresses with a single process")
    parser.add_argument("app", nargs="?", default="api.main:app")
    parser.add_argument("--host", action="append", help="repeatable (default: API_HOST, a comma-separated list)")
    parser.add_argument("--port", type=int, default=API_PORT)
    args = parser.parse_args()

    # trusting every address would let anybody choose the address the limits and the lockout see
    if any(p.strip() in ("*", "0.0.0.0/0", "::/0") for p in FORWARDED_ALLOW_IPS.split(",")):
        raise SystemExit("FORWARDED_ALLOW_IPS must list the proxy addresses, not every address")
    hosts = args.host or [h.strip() for h in API_HOST.split(",") if h.strip()]
    sockets = [_listen(h, args.port) for h in hosts]
    # Config also configures uvicorn's logging
    # On SIGTERM uvicorn stops accepting and waits for the requests in flight; a streamed answer
    # can last minutes, so the wait is bounded (seconds) and the rest is cut short
    config = uvicorn.Config(args.app, forwarded_allow_ips=FORWARDED_ALLOW_IPS,
                            timeout_graceful_shutdown=int(os.getenv("SHUTDOWN_GRACE_SECONDS", "15")))
    log = logging.getLogger("uvicorn.error")
    log.info("Listening on port %d of %s", args.port, ", ".join(hosts))
    log.info("X-Forwarded-For accepted from %s", FORWARDED_ALLOW_IPS)
    uvicorn.Server(config).run(sockets=sockets)


if __name__ == "__main__":
    main()
