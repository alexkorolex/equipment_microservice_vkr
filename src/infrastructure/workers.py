from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable


class PeriodicWorker:
    """Runs ``job`` every ``interval_seconds`` until ``stop_event`` is set.

    A failed run is logged and the next one starts on schedule, so one bad cycle
    never stops the worker.
    """

    def __init__(
        self,
        name: str,
        job: Callable[[], Awaitable[int]],
        interval_seconds: float,
        logger: logging.Logger,
    ) -> None:
        self._name = name
        self._job = job
        self._interval_seconds = interval_seconds
        self._logger = logger

    async def run(self, stop_event: asyncio.Event) -> None:
        self._logger.info("Worker started", extra={"worker": self._name})
        while not stop_event.is_set():
            await self._run_once()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._interval_seconds)
            except TimeoutError:
                continue
        self._logger.info("Worker stopped", extra={"worker": self._name})

    async def _run_once(self) -> None:
        try:
            processed = await self._job()
        except Exception:
            self._logger.exception("Worker run failed", extra={"worker": self._name})
            return
        if processed:
            self._logger.info("Worker run done", extra={"worker": self._name, "processed": processed})
