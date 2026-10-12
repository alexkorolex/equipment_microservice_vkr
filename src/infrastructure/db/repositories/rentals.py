from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from src.application.equipment.commands import OutboxKind, RentalFilter
from src.domain.equipment.errors import (
    EquipmentUnavailableError,
    NotFoundError,
    RentalStateError,
)
from src.domain.equipment.models import (
    EquipmentStatus,
    Rental,
    RentalRequest,
    RentalStatus,
)
from src.infrastructure.db.repositories.mappers import (
    EQUIPMENT_COLUMNS,
    RENTAL_COLUMNS,
    Row,
    equipment_from_row,
    rental_from_row,
)

_SELECT = f"SELECT {RENTAL_COLUMNS} FROM equipment.rentals r"

# An open rental holds the equipment until it comes back, even past its planned end.
_OVERLAP = """
    SELECT 1 FROM equipment.rentals
    WHERE equipment_id = $1 AND status = 'open'
      AND starts_at < $3 AND GREATEST(ends_at, $4) > $2
    LIMIT 1
"""


class PostgresRentalRepository:
    def __init__(self, pool: asyncpg.Pool, id_factory: Callable[[], UUID] = uuid4) -> None:
        self._pool = pool
        self._id_factory = id_factory

    async def open(self, request: RentalRequest, now: datetime) -> Rental:
        async with self._pool.acquire() as connection, connection.transaction():
            equipment = await self._lock_equipment(connection, request)
            busy = await connection.fetchval(
                _OVERLAP, request.equipment_id, request.starts_at, request.ends_at, now
            )
            if busy:
                raise EquipmentUnavailableError("Equipment is already rented for this period")
            row = await self._insert(connection, request)
            rental = rental_from_row(row)
            await self._enqueue(connection, rental, OutboxKind.RENTAL_OPENED, {
                **self._rental_payload(rental),
                "equipment": {
                    "id": str(equipment["id"]),
                    "inventoryNumber": equipment["inventory_number"],
                    "name": equipment["name"],
                    "catalogItemId": equipment["catalog_item_id"],
                },
            })
        return rental

    async def get(self, organization_id: int, rental_id: UUID) -> Rental:
        row = await self._pool.fetchrow(
            f"{_SELECT} WHERE r.id = $1 AND r.organization_id = $2", rental_id, organization_id
        )
        if row is None:
            raise NotFoundError("Rental not found")
        return rental_from_row(row)

    async def list(self, query: RentalFilter) -> list[Rental]:
        rows = await self._pool.fetch(
            f"""{_SELECT}
            WHERE r.organization_id = $1
              AND ($2::uuid IS NULL OR r.equipment_id = $2)
              AND ($3::bigint IS NULL OR r.worker_user_id = $3)
              AND ($4::text IS NULL OR r.status = $4)
            ORDER BY r.starts_at DESC LIMIT $5 OFFSET $6""",
            query.organization_id,
            query.equipment_id,
            query.worker_user_id,
            query.status.value if query.status else None,
            query.limit,
            query.offset,
        )
        return [rental_from_row(row) for row in rows]

    async def return_equipment(
        self, organization_id: int, rental_id: UUID, returned_at: datetime
    ) -> Rental:
        async with self._pool.acquire() as connection, connection.transaction():
            rental = await self._lock_open(connection, organization_id, rental_id)
            if returned_at < rental.starts_at:
                raise RentalStateError("The rental has not started yet: cancel it instead")
            accrued_until = await connection.fetchval(
                "SELECT max(period_end) FROM equipment.cost_accruals WHERE rental_id = $1",
                rental_id,
            )
            if accrued_until is not None and returned_at < accrued_until:
                raise RentalStateError("Costs are already accrued past this return time")
            updated = await self._set_status(
                connection, rental_id, RentalStatus.RETURNED, returned_at
            )
            await self._enqueue(connection, updated, OutboxKind.RENTAL_RETURNED, {
                **self._rental_payload(updated),
                "returnedAt": returned_at.isoformat(),
            })
        return updated

    async def cancel(self, organization_id: int, rental_id: UUID, now: datetime) -> Rental:
        async with self._pool.acquire() as connection, connection.transaction():
            rental = await self._lock_open(connection, organization_id, rental_id)
            if rental.starts_at <= now:
                raise RentalStateError("The rental has already started: return the equipment")
            updated = await self._set_status(connection, rental_id, RentalStatus.CANCELLED, None)
            await self._enqueue(
                connection, updated, OutboxKind.RENTAL_CANCELLED, self._rental_payload(updated)
            )
        return updated

    @staticmethod
    async def _lock_equipment(connection: asyncpg.Connection, request: RentalRequest) -> Row:
        row: Row | None = await connection.fetchrow(
            f"""SELECT {EQUIPMENT_COLUMNS} FROM equipment.equipment e
            WHERE e.id = $1 AND e.organization_id = $2 FOR UPDATE""",
            request.equipment_id,
            request.organization_id,
        )
        if row is None:
            raise NotFoundError("Equipment not found")
        if equipment_from_row(row).status is not EquipmentStatus.AVAILABLE:
            raise EquipmentUnavailableError(f"Equipment is {row['status']}")
        return row

    async def _insert(self, connection: asyncpg.Connection, request: RentalRequest) -> Row:
        row: Row = await connection.fetchrow(
            """
            INSERT INTO equipment.rentals AS r (
                id, organization_id, equipment_id, worker_user_id, starts_at, ends_at,
                status, shift_hours_per_day, purpose, created_by_user_id
            ) VALUES ($1, $2, $3, $4, $5, $6, 'open', $7, $8, $9)
            RETURNING """
            + RENTAL_COLUMNS,
            self._id_factory(),
            request.organization_id,
            request.equipment_id,
            request.worker_user_id,
            request.starts_at,
            request.ends_at,
            request.shift_hours_per_day,
            request.purpose,
            request.created_by_user_id,
        )
        return row

    @staticmethod
    async def _lock_open(
        connection: asyncpg.Connection, organization_id: int, rental_id: UUID
    ) -> Rental:
        row = await connection.fetchrow(
            f"{_SELECT} WHERE r.id = $1 AND r.organization_id = $2 FOR UPDATE",
            rental_id,
            organization_id,
        )
        if row is None:
            raise NotFoundError("Rental not found")
        rental = rental_from_row(row)
        if rental.status is not RentalStatus.OPEN:
            raise RentalStateError(f"The rental is already {rental.status.value}")
        return rental

    @staticmethod
    async def _set_status(
        connection: asyncpg.Connection,
        rental_id: UUID,
        status: RentalStatus,
        returned_at: datetime | None,
    ) -> Rental:
        row = await connection.fetchrow(
            """UPDATE equipment.rentals AS r
            SET status = $2, returned_at = $3, updated_at = now()
            WHERE r.id = $1 RETURNING """
            + RENTAL_COLUMNS,
            rental_id,
            status.value,
            returned_at,
        )
        return rental_from_row(row)

    async def _enqueue(
        self,
        connection: asyncpg.Connection,
        rental: Rental,
        kind: OutboxKind,
        payload: dict[str, Any],
    ) -> None:
        await connection.execute(
            """INSERT INTO equipment.outbox (id, rental_id, kind, payload)
            VALUES ($1, $2, $3, $4)""",
            self._id_factory(),
            rental.id,
            kind.value,
            payload,
        )

    @staticmethod
    def _rental_payload(rental: Rental) -> dict[str, Any]:
        return {
            "rentalId": str(rental.id),
            "organizationId": rental.organization_id,
            "workerUserId": rental.worker_user_id,
            "startsAt": rental.starts_at.isoformat(),
            "endsAt": rental.ends_at.isoformat(),
            "shiftHoursPerDay": str(rental.shift_hours_per_day),
            "purpose": rental.purpose,
        }
