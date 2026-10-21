"""
Shared rate limiter instance (slowapi).

Extracted into a module of its own so it can be imported by both api.main and
api.auth without creating circular imports.
"""

from slowapi import Limiter

from api.clientkey import client_key

limiter = Limiter(key_func=client_key)
