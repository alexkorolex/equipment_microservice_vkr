from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from src.application.equipment.commands import OutboxKind, OutboxMessage
from src.application.equipment.errors import CostSystemRejectedError, CostSystemUnavailableError
from src.application.equipment.ports import Clock, CostSystem, OutboxRepository


class SubmissionService:
    """Delivers rental changes from the outbox to the cost system.

    Unavailability is retried with exponential backoff up to ``max_attempts``; a
    rejection marks the message failed at once. Messages of one rental go in order.
    """

    _BASE_DELAY = timedelta(seconds=5)
    _MAX_DELAY = timedelta(hours=1)

    def __init__(
        self,
        outbox: OutboxRepository,
        cost_system: CostSystem,
        clock: Clock,
        logger: logging.Logger,
        batch_size: int,
        max_attempts: int,
    ) -> None:
        self._outbox = outbox
        self._cost_system = cost_system
        self._clock = clock
        self._logger = logger
        self._batch_size = batch_size
        self._max_attempts = max_attempts

    async def run_once(self) -> int:
        messages = await self._outbox.claim(self._clock.now(), self._batch_size)
        delivered = 0
        for message in messages:
            delivered += await self._deliver(message)
        return delivered

    async def _deliver(self, message: OutboxMessage) -> int:
        context = self._context(message)
        try:
            result = await self._send(message)
        except CostSystemUnavailableError as exc:
            await self._postpone(message, str(exc), context)
            return 0
        except CostSystemRejectedError as exc:
            await self._outbox.mark_failed(message, str(exc))
            self._logger.error("Cost system rejected rental", extra={**context, "error": str(exc)})
            return 0
        await self._outbox.mark_sent(message, result)
        self._logger.info("Rental delivered to cost system", extra=context)
        return 1

    async def _send(self, message: OutboxMessage) -> dict[str, Any]:
        if message.kind is OutboxKind.RENTAL_OPENED:
            return await self._cost_system.register_rental(message.payload)
        if message.kind is OutboxKind.RENTAL_RETURNED:
            return await self._cost_system.notify_return(message.payload)
        return await self._cost_system.notify_cancel(message.payload)

    async def _postpone(self, message: OutboxMessage, error: str, context: dict[str, Any]) -> None:
        attempts = message.attempts + 1
        if attempts >= self._max_attempts:
            await self._outbox.mark_failed(message, error)
            self._logger.error("Rental delivery gave up", extra={**context, "error": error})
            return
        delay = min(self._BASE_DELAY * 2 ** (attempts - 1), self._MAX_DELAY)
        await self._outbox.retry_later(message, error, self._clock.now() + delay)
        self._logger.warning(
            "Rental delivery postponed",
            extra={**context, "error": error, "retry_in_s": delay.total_seconds()},
        )

    @staticmethod
    def _context(message: OutboxMessage) -> dict[str, Any]:
        return {
            "operation": "submit_rental",
            "aggregate_id": str(message.rental_id),
            "message_id": str(message.id),
            "kind": message.kind.value,
            "attempt": message.attempts + 1,
        }
