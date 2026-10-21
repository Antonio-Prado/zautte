"""
Limit on the size of a request body, enforced while it is read.

FastAPI reads and parses a whole JSON body before the dependencies (login, quota) run,
so a very large POST would be buffered in memory by anybody, with no account. Nothing
the API accepts is larger than a few kilobytes: a declared size over the limit is refused at
once (413), and a body that grows past it while streaming is cut (413).
"""

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from config.settings import MAX_BODY_BYTES


class BodyLimitMiddleware:
    def __init__(self, app, max_bytes: int = MAX_BODY_BYTES):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS"):
            return await self.app(scope, receive, send)
        declared = dict(scope["headers"]).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > self.max_bytes:
            return await JSONResponse({"detail": "Richiesta troppo grande"}, status_code=413)(scope, receive, send)
        seen = 0

        async def limited_receive():
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_bytes:
                    raise HTTPException(status_code=413, detail="Richiesta troppo grande")
            return message

        await self.app(scope, limited_receive, send)
