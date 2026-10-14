"""Run coroutines to completion on a worker thread, cancellable from any other thread."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Coroutine
from typing import Any, TypeVar

T = TypeVar("T")


class CancellableRunner:
    """Like asyncio.run(), but the running job can be cancelled from another thread.

    run() belongs to one worker thread; cancel() is safe to call from anywhere (e.g. the GUI
    thread). A cancel() that lands between two run() calls of the same job (listen -> think ->
    speak) is remembered, so the next run() raises CancelledError instead of the request being lost.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        self._cancel_requested = False

    def begin(self) -> None:
        """Call at the start of every job to clear a stale cancel request."""
        with self._lock:
            self._cancel_requested = False

    def run(self, coro: Coroutine[Any, Any, T]) -> T:
        with self._lock:
            if self._cancel_requested:
                coro.close()
                raise asyncio.CancelledError()
            loop = asyncio.new_event_loop()
            task = loop.create_task(coro)
            self._loop, self._task = loop, task
        try:
            return loop.run_until_complete(task)
        finally:
            with self._lock:
                self._loop = self._task = None
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            finally:
                loop.close()

    def cancel(self) -> None:
        with self._lock:
            self._cancel_requested = True
            loop, task = self._loop, self._task
        if loop is not None and task is not None:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:  # loop closed in the instant between the lock and here
                pass