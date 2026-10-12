from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from src.application.equipment.commands import (
    AccrualCandidate,
    CostCalculation,
    CostCalculationRequest,
    EquipmentChanges,
    ListQuery,
    OutboxMessage,
    RentalFilter,
)
from src.domain.equipment.models import (
    Equipment,
    EquipmentSpec,
    EquipmentStatus,
    Rental,
    RentalCosts,
    RentalRequest,
)
from src.domain.equipment.periods import AccrualPeriod


class Clock(Protocol):
    def now(self) -> datetime: ...


class EquipmentRepository(Protocol):
    async def create(self, spec: EquipmentSpec, created_by_user_id: int) -> Equipment: ...

    async def get(self, organization_id: int, equipment_id: UUID) -> Equipment: ...

    async def update(
        self,
        organization_id: int,
        equipment_id: UUID,
        changes: EquipmentChanges,
        expected_version: int | None,
    ) -> Equipment: ...

    async def list(
        self, query: ListQuery, status: EquipmentStatus | None
    ) -> list[Equipment]: ...


class RentalRepository(Protocol):
    """Rental changes are atomic with their outbox message for the cost system."""

    async def open(self, request: RentalRequest, now: datetime) -> Rental: ...

    async def get(self, organization_id: int, rental_id: UUID) -> Rental: ...

    async def list(self, query: RentalFilter) -> list[Rental]: ...

    async def return_equipment(
        self, organization_id: int, rental_id: UUID, returned_at: datetime
    ) -> Rental: ...

    async def cancel(self, organization_id: int, rental_id: UUID, now: datetime) -> Rental: ...


class CostRepository(Protocol):
    async def costs(self, organization_id: int, rental_id: UUID) -> RentalCosts: ...

    async def accrual_candidates(self, now: datetime, limit: int) -> list[AccrualCandidate]: ...

    async def save_accrual(
        self, rental_id: UUID, period: AccrualPeriod, calculation: CostCalculation
    ) -> bool: ...

    async def mark_complete(self, rental_id: UUID) -> None: ...


class OutboxRepository(Protocol):
    async def claim(self, now: datetime, limit: int) -> list[OutboxMessage]: ...

    async def mark_sent(self, message: OutboxMessage, result: dict[str, Any]) -> None: ...

    async def retry_later(
        self, message: OutboxMessage, error: str, next_attempt_at: datetime
    ) -> None: ...

    async def mark_failed(self, message: OutboxMessage, error: str) -> None: ...


class CostSystem(Protocol):
    """The external system: registers rentals and calculates their running costs.

    Raises CostSystemUnavailableError when a call may be repeated later and
    CostSystemRejectedError when it rejected the data.
    """

    async def register_rental(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    async def notify_return(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    async def notify_cancel(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    async def calculate(self, request: CostCalculationRequest) -> CostCalculation: ...
