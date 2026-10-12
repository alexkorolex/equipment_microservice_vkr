from __future__ import annotations

import logging
from datetime import datetime, timedelta
from uuid import UUID

from src.application.equipment.commands import RentalFilter
from src.application.equipment.ports import Clock, CostRepository, RentalRepository
from src.domain.equipment.errors import InvalidDataError
from src.domain.equipment.models import Rental, RentalCosts, RentalRequest, require_aware


class RentalService:
    """Rentals of equipment to workers; the cost system learns of them via the outbox."""

    # Allowed clock difference between the caller and this service.
    _CLOCK_SKEW = timedelta(minutes=1)

    def __init__(
        self,
        rentals: RentalRepository,
        costs: CostRepository,
        clock: Clock,
        logger: logging.Logger,
    ) -> None:
        self._rentals = rentals
        self._costs = costs
        self._clock = clock
        self._logger = logger

    async def open(self, request: RentalRequest) -> Rental:
        rental = await self._rentals.open(request, self._clock.now())
        self._log("Rental opened", rental, "open_rental", request.created_by_user_id)
        return rental

    async def get(self, organization_id: int, rental_id: UUID) -> Rental:
        return await self._rentals.get(organization_id, rental_id)

    async def list(self, query: RentalFilter) -> list[Rental]:
        return await self._rentals.list(query)

    async def return_equipment(
        self,
        organization_id: int,
        rental_id: UUID,
        returned_at: datetime | None,
        actor_user_id: int,
    ) -> Rental:
        now = self._clock.now()
        moment = require_aware(returned_at, "returnedAt") if returned_at else now
        if moment > now + self._CLOCK_SKEW:
            raise InvalidDataError("returnedAt must not be in the future")
        rental = await self._rentals.return_equipment(organization_id, rental_id, min(moment, now))
        self._log("Equipment returned", rental, "return_equipment", actor_user_id)
        return rental

    async def cancel(self, organization_id: int, rental_id: UUID, actor_user_id: int) -> Rental:
        rental = await self._rentals.cancel(organization_id, rental_id, self._clock.now())
        self._log("Rental cancelled", rental, "cancel_rental", actor_user_id)
        return rental

    async def costs(self, organization_id: int, rental_id: UUID) -> RentalCosts:
        return await self._costs.costs(organization_id, rental_id)

    def _log(self, message: str, rental: Rental, operation: str, actor_id: int) -> None:
        self._logger.info(
            message,
            extra={
                "operation": operation,
                "organization_id": rental.organization_id,
                "aggregate_id": str(rental.id),
                "equipment_id": str(rental.equipment_id),
                "worker_user_id": rental.worker_user_id,
                "actor_id": actor_id,
            },
        )
