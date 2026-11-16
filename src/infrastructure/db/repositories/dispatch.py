from __future__ import annotations

import builtins
from datetime import datetime
from uuid import UUID

import asyncpg

from src.application.equipment.commands import DispatchQuery
from src.domain.equipment.models import DispatchEquipment, Rental
from src.infrastructure.db.repositories.leases import PostgresLeaseRepository
from src.infrastructure.db.repositories.mappers import (
    EQUIPMENT_COLUMNS,
    RENTAL_COLUMNS,
    equipment_from_row,
    rental_from_row,
)


class PostgresDispatchRepository:
    """Equipment of several organizations, as the gateway selected it for a dispatcher."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self._leases = PostgresLeaseRepository(pool)

    async def list(self, query: DispatchQuery, now: datetime) -> list[DispatchEquipment]:
        if not query.selections or not query.territory_ids:
            return []
        whole = [item.organization_id for item in query.selections if item.all_equipment]
        units = [
            (equipment_id, item.organization_id)
            for item in query.selections
            if not item.all_equipment
            for equipment_id in item.equipment_ids
        ]
        rows = await self._pool.fetch(
            f"""SELECT {EQUIPMENT_COLUMNS} FROM equipment.equipment e
            WHERE e.territory_id = ANY($1::bigint[])
              AND (e.organization_id = ANY($2::bigint[])
                   OR (e.id, e.organization_id) IN (
                       SELECT * FROM unnest($3::uuid[], $4::bigint[])))
              AND ($5::text IS NULL OR e.status = $5)
            ORDER BY e.organization_id, e.inventory_number LIMIT $6 OFFSET $7""",
            list(query.territory_ids),
            whole,
            [unit[0] for unit in units],
            [unit[1] for unit in units],
            query.status.value if query.status else None,
            query.limit,
            query.offset,
        )
        equipment = [equipment_from_row(row) for row in rows]
        ids = [item.id for item in equipment]
        rentals = await self._current_rentals(ids, now)
        leases = await self._leases.current_for(ids, now)
        return [
            DispatchEquipment(item, rentals.get(item.id), leases.get(item.id))
            for item in equipment
        ]

    async def _current_rentals(
        self, ids: builtins.list[UUID], now: datetime
    ) -> dict[UUID, Rental]:
        """The open rental holding each unit: the started one, or else the next one."""
        rows = await self._pool.fetch(
            f"""SELECT DISTINCT ON (r.equipment_id) {RENTAL_COLUMNS}
            FROM equipment.rentals r
            WHERE r.equipment_id = ANY($1::uuid[]) AND r.status = 'open'
            ORDER BY r.equipment_id, r.starts_at > $2, r.starts_at""",
            ids,
            now,
        )
        return {row["equipment_id"]: rental_from_row(row) for row in rows}
