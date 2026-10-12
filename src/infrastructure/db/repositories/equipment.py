from __future__ import annotations

import dataclasses
from collections.abc import Callable
from uuid import UUID, uuid4

import asyncpg

from src.application.equipment.commands import EquipmentChanges, ListQuery
from src.domain.equipment.errors import NotFoundError, VersionConflictError
from src.domain.equipment.models import Equipment, EquipmentSpec, EquipmentStatus
from src.infrastructure.db.repositories.mappers import EQUIPMENT_COLUMNS, equipment_from_row

_SELECT = f"SELECT {EQUIPMENT_COLUMNS} FROM equipment.equipment e"


class PostgresEquipmentRepository:
    def __init__(self, pool: asyncpg.Pool, id_factory: Callable[[], UUID] = uuid4) -> None:
        self._pool = pool
        self._id_factory = id_factory

    async def create(self, spec: EquipmentSpec, created_by_user_id: int) -> Equipment:
        row = await self._pool.fetchrow(
            """
            INSERT INTO equipment.equipment AS e (
                id, organization_id, inventory_number, name, catalog_item_id,
                purchase_price, residual_value, useful_life_months, fuel_type,
                fuel_consumption_per_hour, daily_overhead, currency, created_by_user_id
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
            RETURNING """
            + EQUIPMENT_COLUMNS,
            self._id_factory(),
            *self._spec_values(spec),
            created_by_user_id,
        )
        return equipment_from_row(row)

    async def get(self, organization_id: int, equipment_id: UUID) -> Equipment:
        row = await self._pool.fetchrow(
            f"{_SELECT} WHERE e.id = $1 AND e.organization_id = $2",
            equipment_id,
            organization_id,
        )
        if row is None:
            raise NotFoundError("Equipment not found")
        return equipment_from_row(row)

    async def update(
        self,
        organization_id: int,
        equipment_id: UUID,
        changes: EquipmentChanges,
        expected_version: int | None,
    ) -> Equipment:
        async with self._pool.acquire() as connection, connection.transaction():
            current = await self._locked(connection, organization_id, equipment_id)
            if expected_version is not None and expected_version != current.version:
                raise VersionConflictError("Equipment was changed by someone else")
            spec = dataclasses.replace(current.spec, **changes.spec_changes())
            return await self._write(connection, current.id, spec, changes.status or current.status)

    @staticmethod
    async def _locked(
        connection: asyncpg.Connection, organization_id: int, equipment_id: UUID
    ) -> Equipment:
        row = await connection.fetchrow(
            f"{_SELECT} WHERE e.id = $1 AND e.organization_id = $2 FOR UPDATE",
            equipment_id,
            organization_id,
        )
        if row is None:
            raise NotFoundError("Equipment not found")
        return equipment_from_row(row)

    async def _write(
        self,
        connection: asyncpg.Connection,
        equipment_id: UUID,
        spec: EquipmentSpec,
        status: EquipmentStatus,
    ) -> Equipment:
        row = await connection.fetchrow(
            """
            UPDATE equipment.equipment AS e SET
                name = $3, catalog_item_id = $4, purchase_price = $5, residual_value = $6,
                useful_life_months = $7, fuel_type = $8, fuel_consumption_per_hour = $9,
                daily_overhead = $10, status = $11, version = e.version + 1, updated_at = now()
            WHERE e.id = $1 AND e.organization_id = $2
            RETURNING """
            + EQUIPMENT_COLUMNS,
            equipment_id,
            *self._spec_values(spec)[:1],
            *self._spec_values(spec)[2:-1],
            status.value,
        )
        return equipment_from_row(row)

    async def list(self, query: ListQuery, status: EquipmentStatus | None) -> list[Equipment]:
        rows = await self._pool.fetch(
            f"""{_SELECT}
            WHERE e.organization_id = $1 AND ($2::text IS NULL OR e.status = $2)
            ORDER BY e.inventory_number LIMIT $3 OFFSET $4""",
            query.organization_id,
            status.value if status else None,
            query.limit,
            query.offset,
        )
        return [equipment_from_row(row) for row in rows]

    @staticmethod
    def _spec_values(spec: EquipmentSpec) -> tuple[object, ...]:
        """Values in column order, from organization_id to currency."""
        return (
            spec.organization_id,
            spec.inventory_number,
            spec.name,
            spec.catalog_item_id,
            spec.purchase_price,
            spec.residual_value,
            spec.useful_life_months,
            spec.fuel_type.value,
            spec.fuel_consumption_per_hour,
            spec.daily_overhead,
            spec.currency,
        )
