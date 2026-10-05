"""
Avvio di produzione: un solo processo uvicorn in ascolto su IPv4 e IPv6.

uvicorn da riga di comando apre un solo indirizzo e su FreeBSD «::» non accetta
connessioni IPv4 (net.inet6.ip6.v6only=1), così start.sh lanciava due processi,
uno per famiglia di indirizzi, con memoria separata: ognuno caricava il proprio
vector store, aveva la sua cache e i suoi contatori e riscriveva a turno
data/stats.json, perdendo i conti dell'altro. Qui si aprono i due socket e li
si passa a un solo server.

    python -m api.serve api.main:app --host 0.0.0.0 --host :: --port 8000

Senza --host ascolta solo in locale (127.0.0.1 e ::1): l'apertura a tutta la
rete è una scelta esplicita di chi lo avvia (start.sh in produzione).

L'app compare come argomento perché i comandi che cercano il processo dell'API
(scripts/sync.py, scripts/watchdog.sh, scripts/chatbot_rcd) usano «api.main:app».
"""

import argparse
import logging
import socket

import uvicorn


def _listen(host: str, port: int) -> socket.socket:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if family == socket.AF_INET6:
        # Solo IPv6: le connessioni IPv4 hanno il loro socket (vale anche dove il
        # sistema, come macOS e Linux, aprirebbe «::» a entrambe le famiglie).
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
    config = uvicorn.Config(args.app)   # configura anche il logging di uvicorn
    logging.getLogger("uvicorn.error").info("In ascolto sulla porta %d di %s", args.port, ", ".join(hosts))
    uvicorn.Server(config).run(sockets=sockets)


if __name__ == "__main__":
    main()
