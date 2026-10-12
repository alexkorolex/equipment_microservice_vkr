from __future__ import annotations

import logging
from uuid import UUID

from src.application.equipment.commands import EquipmentChanges, ListQuery
from src.application.equipment.ports import EquipmentRepository
from src.domain.equipment.models import Equipment, EquipmentSpec, EquipmentStatus


class EquipmentService:
    def __init__(self, repository: EquipmentRepository, logger: logging.Logger) -> None:
        self._repository = repository
        self._logger = logger

    async def create(self, spec: EquipmentSpec, created_by_user_id: int) -> Equipment:
        equipment = await self._repository.create(spec, created_by_user_id)
        self._logger.info(
            "Equipment registered",
            extra={
                "operation": "create_equipment",
                "organization_id": spec.organization_id,
                "aggregate_id": str(equipment.id),
                "actor_id": created_by_user_id,
            },
        )
        return equipment

    async def update(
        self,
        organization_id: int,
        equipment_id: UUID,
        changes: EquipmentChanges,
        expected_version: int | None,
    ) -> Equipment:
        equipment = await self._repository.update(
            organization_id, equipment_id, changes, expected_version
        )
        self._logger.info(
            "Equipment updated",
            extra={
                "operation": "update_equipment",
                "organization_id": organization_id,
                "aggregate_id": str(equipment_id),
                "status": equipment.status.value,
            },
        )
        return equipment

    async def get(self, organization_id: int, equipment_id: UUID) -> Equipment:
        return await self._repository.get(organization_id, equipment_id)

    async def list(self, query: ListQuery, status: EquipmentStatus | None) -> list[Equipment]:
        return await self._repository.list(query, status)
