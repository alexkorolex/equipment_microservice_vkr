from __future__ import annotations

import builtins
import logging
from datetime import datetime, timedelta
from uuid import UUID

from src.application.equipment.commands import DispatchQuery, LeaseFilter
from src.application.equipment.ports import Clock, DispatchRepository, LeaseRepository
from src.domain.equipment.errors import InvalidDataError
from src.domain.equipment.models import DispatchEquipment, Lease, LeaseRequest


class LeaseService:
    """Leases of units between organizations; the gateway checks the dispatcher's rights."""

    # Allowed clock difference between the caller and this service.
    _CLOCK_SKEW = timedelta(minutes=1)

    def __init__(
        self,
        leases: LeaseRepository,
        dispatch: DispatchRepository,
        clock: Clock,
        logger: logging.Logger,
    ) -> None:
        self._leases = leases
        self._dispatch = dispatch
        self._clock = clock
        self._logger = logger

    @property
    def now(self) -> datetime:
        return self._clock.now()

    async def create(self, request: LeaseRequest) -> Lease:
        now = self._clock.now()
        if request.starts_at < now - self._CLOCK_SKEW:
            raise InvalidDataError("startsAt must not be in the past")
        if request.ends_at <= now:
            raise InvalidDataError("endsAt must be in the future")
        lease = await self._leases.create(request, now)
        self._log("Lease created", lease, "create_lease", request.created_by_user_id)
        return lease

    async def end(self, lease_id: UUID, actor_user_id: int, reason: str) -> Lease:
        reason = reason.strip()
        if len(reason) > 1000:
            raise InvalidDataError("reason is too long")
        lease = await self._leases.end(lease_id, actor_user_id, reason, self._clock.now())
        self._log("Lease ended", lease, "end_lease", actor_user_id)
        return lease

    async def get(self, lease_id: UUID) -> Lease:
        return await self._leases.get(lease_id)

    async def list(self, query: LeaseFilter) -> list[Lease]:
        return await self._leases.list(query, self._clock.now())

    async def dispatch(self, query: DispatchQuery) -> builtins.list[DispatchEquipment]:
        return await self._dispatch.list(query, self._clock.now())

    def _log(self, message: str, lease: Lease, operation: str, actor_id: int) -> None:
        self._logger.info(
            message,
            extra={
                "operation": operation,
                "organization_id": lease.owner_organization_id,
                "lessee_organization_id": lease.lessee_organization_id,
                "aggregate_id": str(lease.id),
                "equipment_id": str(lease.equipment_id),
                "actor_id": actor_id,
            },
        )
