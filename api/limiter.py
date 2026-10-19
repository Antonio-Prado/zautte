"""
Shared rate limiter instance (slowapi).

Extracted into a module of its own so it can be imported by both api.main and
api.auth without creating circular imports.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
