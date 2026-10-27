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
        values = self._spec_values(spec)
        columns = ", ".join(values)
        placeholders = ", ".join(f"${index}" for index in range(2, len(values) + 2))
        row = await self._pool.fetchrow(
            f"""
            INSERT INTO equipment.equipment AS e (id, {columns}, created_by_user_id)
            VALUES ($1, {placeholders}, ${len(values) + 2})
            RETURNING {EQUIPMENT_COLUMNS}""",
            self._id_factory(),
            *values.values(),
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
        # The organization, the inventory number and the currency never change.
        values = {
            name: value
            for name, value in self._spec_values(spec).items()
            if name not in {"organization_id", "inventory_number", "currency"}
        }
        values["status"] = status.value
        assignments = ", ".join(
            f"{name} = ${index}" for index, name in enumerate(values, start=3)
        )
        row = await connection.fetchrow(
            f"""
            UPDATE equipment.equipment AS e SET
                {assignments}, version = e.version + 1, updated_at = now()
            WHERE e.id = $1 AND e.organization_id = $2
            RETURNING {EQUIPMENT_COLUMNS}""",
            equipment_id,
            spec.organization_id,
            *values.values(),
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

    async def linked(self, organization_id: int) -> dict[int, UUID]:
        """Equipment by the FortMonitor object it is tracked by."""
        rows = await self._pool.fetch(
            """SELECT telematics_object_id, id FROM equipment.equipment
            WHERE organization_id = $1 AND telematics_object_id IS NOT NULL""",
            organization_id,
        )
        return {int(row["telematics_object_id"]): row["id"] for row in rows}

    @staticmethod
    def _spec_values(spec: EquipmentSpec) -> dict[str, object]:
        """Column values of the spec, in column order."""
        return {
            "organization_id": spec.organization_id,
            "inventory_number": spec.inventory_number,
            "name": spec.name,
            "catalog_item_id": spec.catalog_item_id,
            "purchase_price": spec.purchase_price,
            "residual_value": spec.residual_value,
            "useful_life_months": spec.useful_life_months,
            "fuel_type": spec.fuel_type.value,
            "fuel_consumption_per_hour": spec.fuel_consumption_per_hour,
            "daily_overhead": spec.daily_overhead,
            "currency": spec.currency,
            "registration_number": spec.registration_number,
            "vehicle_type_id": spec.vehicle_type_id,
            "vehicle_type_name": spec.vehicle_type_name,
            "fuel_consumption_mode": spec.fuel_consumption_mode.value,
            "fuel_consumption_per_100km": spec.fuel_consumption_per_100km,
            "telematics_object_id": spec.telematics_object_id,
        }
