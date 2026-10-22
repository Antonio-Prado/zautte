import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from api.aio import to_thread_uncancellable


async def _cancelled_right_after_scheduling(run):
    """A task that schedules thread work in its `finally` and is cancelled at once, the way the
    streaming response cancels its producer after the last event. The executor has a single
    worker, kept busy, so the work is still pending when the cancellation arrives."""
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
    busy = threading.Event()
    loop.run_in_executor(None, busy.wait)   # occupies the only worker
    done = threading.Event()

    async def producer():
        await run(done.set)   # the write the real producer schedules in its `finally`

    task = asyncio.create_task(producer())
    await asyncio.sleep(0)   # the producer runs up to its await and hands the work to the executor
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    busy.set()   # the worker is free again
    await asyncio.sleep(0.2)
    return done.is_set()


def test_plain_to_thread_loses_the_work():
    assert asyncio.run(_cancelled_right_after_scheduling(asyncio.to_thread)) is False


def test_uncancellable_work_is_done():
    assert asyncio.run(_cancelled_right_after_scheduling(to_thread_uncancellable)) is True
