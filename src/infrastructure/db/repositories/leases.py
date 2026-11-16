from __future__ import annotations

import builtins
from collections.abc import Callable
from datetime import datetime
from uuid import UUID, uuid4

import asyncpg

from src.application.equipment.commands import LeaseFilter, LeaseSide
from src.domain.equipment.errors import (
    EquipmentUnavailableError,
    LeaseStateError,
    NotFoundError,
)
from src.domain.equipment.models import EquipmentStatus, Lease, LeaseRequest
from src.infrastructure.db.repositories.mappers import (
    EQUIPMENT_COLUMNS,
    LEASE_COLUMNS,
    Row,
    equipment_from_row,
    lease_from_row,
)

_SELECT = f"SELECT {LEASE_COLUMNS} FROM equipment.leases l"

# Leases in force or scheduled: not ended early and not past their end.
_CURRENT = "l.ended_at IS NULL AND l.ends_at > $1"


async def _with_equipment(
    connection: asyncpg.Connection | asyncpg.Pool, rows: builtins.list[Row]
) -> builtins.list[Lease]:
    """Leases with their units (a join would mix up the two ``id`` columns)."""
    units = await connection.fetch(
        f"SELECT {EQUIPMENT_COLUMNS} FROM equipment.equipment e WHERE e.id = ANY($1::uuid[])",
        list({row["equipment_id"] for row in rows}),
    )
    equipment = {unit["id"]: equipment_from_row(unit) for unit in units}
    return [lease_from_row(row, equipment.get(row["equipment_id"])) for row in rows]


class PostgresLeaseRepository:
    """Leases of a unit never overlap and never cover another organization's open rental:
    both are checked under the unit's row lock, which rentals take as well."""

    def __init__(self, pool: asyncpg.Pool, id_factory: Callable[[], UUID] = uuid4) -> None:
        self._pool = pool
        self._id_factory = id_factory

    async def create(self, request: LeaseRequest, now: datetime) -> Lease:
        async with self._pool.acquire() as connection, connection.transaction():
            equipment = await connection.fetchrow(
                f"""SELECT {EQUIPMENT_COLUMNS} FROM equipment.equipment e
                WHERE e.id = $1 AND e.organization_id = $2 FOR UPDATE""",
                request.equipment_id,
                request.owner_organization_id,
            )
            if equipment is None:
                raise NotFoundError("Equipment not found")
            if equipment["status"] == EquipmentStatus.DECOMMISSIONED.value:
                raise EquipmentUnavailableError("Equipment is decommissioned")
            overlapping = await connection.fetchval(
                """SELECT 1 FROM equipment.leases
                WHERE equipment_id = $1 AND ended_at IS NULL
                  AND starts_at < $3 AND ends_at > $2 LIMIT 1""",
                request.equipment_id,
                request.starts_at,
                request.ends_at,
            )
            if overlapping:
                raise LeaseStateError("The unit is already leased for this period")
            # An open rental holds the unit until it comes back, even past its planned end.
            rented = await connection.fetchval(
                """SELECT 1 FROM equipment.rentals
                WHERE equipment_id = $1 AND status = 'open' AND organization_id <> $4
                  AND starts_at < $3 AND GREATEST(ends_at, $5) > $2 LIMIT 1""",
                request.equipment_id,
                request.starts_at,
                request.ends_at,
                request.lessee_organization_id,
                now,
            )
            if rented:
                raise LeaseStateError("The unit has open rentals in this period")
            row = await connection.fetchrow(
                f"""INSERT INTO equipment.leases AS l (
                    id, equipment_id, owner_organization_id, lessee_organization_id,
                    starts_at, ends_at, note, created_by_user_id
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING {LEASE_COLUMNS}""",
                self._id_factory(),
                request.equipment_id,
                request.owner_organization_id,
                request.lessee_organization_id,
                request.starts_at,
                request.ends_at,
                request.note,
                request.created_by_user_id,
            )
        return lease_from_row(row, equipment_from_row(equipment))

    async def end(
        self, lease_id: UUID, actor_user_id: int, reason: str, now: datetime
    ) -> Lease:
        async with self._pool.acquire() as connection, connection.transaction():
            current = await connection.fetchrow(f"{_SELECT} WHERE l.id = $1 FOR UPDATE", lease_id)
            if current is None:
                raise NotFoundError("Lease not found")
            if current["ended_at"] is not None or current["ends_at"] <= now:
                raise LeaseStateError("The lease is already over")
            open_rentals = await connection.fetchval(
                """SELECT count(*) FROM equipment.rentals
                WHERE lease_id = $1 AND status = 'open'""",
                lease_id,
            )
            if open_rentals:
                raise LeaseStateError(
                    "The lessee has open rentals of the unit: close them first"
                )
            await connection.execute(
                """UPDATE equipment.leases
                SET ended_at = $2, ended_by_user_id = $3, end_reason = $4, updated_at = now()
                WHERE id = $1""",
                lease_id,
                now,
                actor_user_id,
                reason,
            )
            row = await connection.fetchrow(f"{_SELECT} WHERE l.id = $1", lease_id)
            leases = await _with_equipment(connection, [row])
        return leases[0]

    async def get(self, lease_id: UUID) -> Lease:
        row = await self._pool.fetchrow(f"{_SELECT} WHERE l.id = $1", lease_id)
        if row is None:
            raise NotFoundError("Lease not found")
        return (await _with_equipment(self._pool, [row]))[0]

    async def list(self, query: LeaseFilter, now: datetime) -> list[Lease]:
        owner = query.side in (None, LeaseSide.OWNER)
        lessee = query.side in (None, LeaseSide.LESSEE)
        rows = await self._pool.fetch(
            f"""{_SELECT}
            WHERE ($2::bigint IS NULL
                   OR ($3 AND l.owner_organization_id = $2)
                   OR ($4 AND l.lessee_organization_id = $2))
              AND (cardinality($5::uuid[]) = 0 OR l.equipment_id = ANY($5::uuid[]))
              AND (NOT $6 OR ({_CURRENT}))
            ORDER BY l.starts_at DESC LIMIT $7 OFFSET $8""",
            now,
            query.organization_id,
            owner,
            lessee,
            list(query.equipment_ids),
            query.current_only,
            query.limit,
            query.offset,
        )
        return await _with_equipment(self._pool, rows)

    async def current_for(
        self, equipment_ids: builtins.list[UUID], now: datetime
    ) -> dict[UUID, Lease]:
        """The lease in force, or else the next scheduled one, of each unit."""
        rows = await self._pool.fetch(
            f"""SELECT DISTINCT ON (l.equipment_id) {LEASE_COLUMNS}
            FROM equipment.leases l
            WHERE l.equipment_id = ANY($2::uuid[]) AND {_CURRENT}
            ORDER BY l.equipment_id, l.starts_at""",
            now,
            equipment_ids,
        )
        return {row["equipment_id"]: lease_from_row(row) for row in rows}
