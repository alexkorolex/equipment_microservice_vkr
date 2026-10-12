from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import asyncpg

from src.application.equipment.commands import OutboxKind, OutboxMessage

# Earliest pending message of each rental only, so a return never overtakes the
# registration. The lease returns a message to the queue if this process dies.
_CLAIM = """
    UPDATE equipment.outbox AS o SET locked_until = $2
    WHERE o.id IN (
        SELECT m.id FROM equipment.outbox m
        WHERE m.status = 'pending' AND m.next_attempt_at <= $1
          AND (m.locked_until IS NULL OR m.locked_until < $1)
          AND NOT EXISTS (
              SELECT 1 FROM equipment.outbox p
              WHERE p.rental_id = m.rental_id AND p.status = 'pending'
                AND p.created_at < m.created_at
          )
        ORDER BY m.created_at
        LIMIT $3
        FOR UPDATE SKIP LOCKED
    )
    RETURNING o.id, o.rental_id, o.kind, o.payload, o.attempts, o.created_at
"""


class PostgresOutboxRepository:
    def __init__(self, pool: asyncpg.Pool, lease: timedelta = timedelta(seconds=60)) -> None:
        self._pool = pool
        self._lease = lease

    async def claim(self, now: datetime, limit: int) -> list[OutboxMessage]:
        rows = await self._pool.fetch(_CLAIM, now, now + self._lease, limit)
        return [
            OutboxMessage(
                id=row["id"],
                rental_id=row["rental_id"],
                kind=OutboxKind(row["kind"]),
                payload=dict(row["payload"]),
                attempts=int(row["attempts"]),
            )
            for row in sorted(rows, key=lambda row: row["created_at"])
        ]

    async def mark_sent(self, message: OutboxMessage, result: dict[str, Any]) -> None:
        async with self._pool.acquire() as connection, connection.transaction():
            await self._finish(connection, message, "sent", None)
            if message.kind is OutboxKind.RENTAL_OPENED:
                reference = result.get("reference")
                await connection.execute(
                    """UPDATE equipment.rentals SET external_status = 'submitted',
                    external_reference = $2, updated_at = now() WHERE id = $1""",
                    message.rental_id,
                    str(reference) if reference is not None else None,
                )

    async def retry_later(
        self, message: OutboxMessage, error: str, next_attempt_at: datetime
    ) -> None:
        await self._pool.execute(
            """UPDATE equipment.outbox SET attempts = attempts + 1, last_error = $2,
            next_attempt_at = $3, locked_until = NULL WHERE id = $1""",
            message.id,
            error[:1000],
            next_attempt_at,
        )

    async def mark_failed(self, message: OutboxMessage, error: str) -> None:
        async with self._pool.acquire() as connection, connection.transaction():
            await self._finish(connection, message, "failed", error)
            if message.kind is OutboxKind.RENTAL_OPENED:
                await connection.execute(
                    """UPDATE equipment.rentals SET external_status = 'failed',
                    updated_at = now() WHERE id = $1""",
                    message.rental_id,
                )

    @staticmethod
    async def _finish(
        connection: asyncpg.Connection, message: OutboxMessage, status: str, error: str | None
    ) -> None:
        await connection.execute(
            """UPDATE equipment.outbox SET status = $2, attempts = attempts + 1,
            last_error = $3, locked_until = NULL,
            sent_at = CASE WHEN $2 = 'sent' THEN now() ELSE sent_at END
            WHERE id = $1""",
            message.id,
            status,
            error[:1000] if error else None,
        )
