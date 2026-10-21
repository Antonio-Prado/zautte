"""
The key under which a client is counted by the rate limits, the admin lockout and the
quota when there is no login.

An IPv4 address is one client. An IPv6 address is not: a home or office connection
gets a whole /64 (2^64 addresses), so counting each address separately would give an
attacker a fresh budget on every request. IPv6 clients are therefore counted by their
/64 network, and an IPv4-mapped IPv6 address (::ffff:1.2.3.4) as the IPv4 address.
"""

import ipaddress

from fastapi import Request


def normalize(host: str) -> str:
    try:
        ip = ipaddress.ip_address((host or "").split("%")[0])
    except ValueError:
        return host or ""
    if ip.version == 6:
        if ip.ipv4_mapped:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network((ip, 64), strict=False))
    return str(ip)


def client_key(request: Request) -> str:
    return normalize(request.client.host) if request.client else ""
