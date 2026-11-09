"""Sends the change events the database triggers record to Kafka "crm.changes".

The realtime-gateway turns them into live updates of the CRM. A message is a signal
that an entity changed (the CRM then reloads it), keyed by the organization so the
events of one organization keep their order. Published at least once.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

import asyncpg
from faststream.kafka import KafkaBroker

TOPIC = "crm.changes"
# Published events are kept a day for inspection, then removed.
RETENTION = timedelta(days=1)


class ChangePublisher:
    def __init__(
        self,
        pool: asyncpg.Pool,
        broker: KafkaBroker,
        table: str,
        batch_size: int = 100,
    ) -> None:
        self._pool = pool
        self._broker = broker
        self._table = table
        self._batch_size = batch_size
        self._logger = logging.getLogger(__name__)

    async def run(self, stop: asyncio.Event, logger: logging.Logger, interval: float = 0.5) -> None:
        """Publishes until ``stop`` is set; a failed batch is retried on the next turn."""
        while not stop.is_set():
            try:
                await self.run_once()
            except Exception:
                logger.exception("Publishing change events failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except TimeoutError:
                continue

    async def run_once(self) -> int:
        """Publishes a batch; the rows stay locked until marked, so a second publisher
        never sends the same events at the same time."""
        async with self._pool.acquire() as connection, connection.transaction():
            rows = await self._pending(connection)
            for row in rows:
                await self._broker.publish(
                    message(row),
                    TOPIC,
                    key=str(row["organization_id"]).encode(),
                    headers={"event": row["event"]},
                )
            await self._mark_or_prune(connection, rows)
        if rows:
            self._logger.debug(
                "Change events published", extra={"table": self._table, "count": len(rows)},
            )
        return len(rows)

    async def _pending(self, connection: asyncpg.Connection) -> list[asyncpg.Record]:
        rows: list[asyncpg.Record] = await connection.fetch(
            f"""
            SELECT id, organization_id, entity, entity_id, event, version, user_id, occurred_at
            FROM {self._table}
            WHERE published_at IS NULL
            ORDER BY occurred_at
            LIMIT $1
            FOR UPDATE SKIP LOCKED
            """,
            self._batch_size,
        )
        return rows

    async def _mark_or_prune(
        self, connection: asyncpg.Connection, rows: list[asyncpg.Record],
    ) -> None:
        if rows:
            await connection.execute(
                f"UPDATE {self._table} SET published_at = now() WHERE id = ANY($1::uuid[])",
                [row["id"] for row in rows],
            )
        else:
            await connection.execute(
                f"DELETE FROM {self._table} WHERE published_at < now() - $1::interval",
                RETENTION,
            )


def message(row: asyncpg.Record | dict[str, Any]) -> dict[str, Any]:
    """The contract of "crm.changes" (see docs/02-contracts.md)."""
    return {
        "event_id": str(row["id"]),
        "event": row["event"],
        "entity": row["entity"],
        "entity_id": row["entity_id"],
        "organization_id": row["organization_id"],
        "version": row["version"],
        # Also visible to this user without the section's permission (a rental's worker).
        "user_id": row["user_id"],
        "occurred_at": row["occurred_at"].isoformat(),
        # Invalidation: the client reloads the entity. Screens that need frequent
        # updates may get the changed fields here later.
        "changes": None,
    }
