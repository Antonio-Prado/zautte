"""
Small asyncio helpers.
"""

import asyncio
from collections.abc import Callable
from typing import Any


async def to_thread_uncancellable(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Runs `func` in a worker thread like asyncio.to_thread(), but the work is done even if the
    awaiting task is cancelled in the meantime.

    asyncio.to_thread() hands the work to the executor and, if the task is cancelled before a
    worker thread has picked it up, the work is cancelled too: nothing runs and nothing is
    logged. That is what happens to a write scheduled in the `finally` of a streaming producer
    that the response generator cancels as soon as the last event is sent. Shielding keeps the
    work alive; the caller still receives CancelledError.
    """
    return await asyncio.shield(asyncio.to_thread(func, *args, **kwargs))
