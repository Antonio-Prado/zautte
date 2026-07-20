"""
Istanza condivisa del rate limiter (slowapi).

Estratta in un modulo a sé per essere importata sia da api.main sia da
api.auth senza creare import circolari.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
