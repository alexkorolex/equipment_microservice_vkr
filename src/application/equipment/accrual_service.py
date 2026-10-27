from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from src.application.equipment.commands import AccrualCandidate, CostCalculationRequest
from src.application.equipment.errors import CostSystemRejectedError, CostSystemUnavailableError
from src.application.equipment.ports import Clock, CostRepository, CostSystem, MileageSource
from src.domain.equipment.errors import (
    TelematicsNotConfiguredError,
    TelematicsRejectedError,
    TelematicsUnavailableError,
)
from src.domain.equipment.mileage import odometer_share
from src.domain.equipment.models import FuelConsumptionMode
from src.domain.equipment.periods import AccrualPeriod, accruals_complete, due_periods


class AccrualService:
    """Asks the cost system for every finished day of a rental and stores the result.

    A day that fails stays due and is asked again on the next run; a stored day is
    never asked twice (one accrual per rental and day). For fuel by mileage the day's km
    come from the tracker, or from the odometer readings once the equipment is back.
    """

    def __init__(
        self,
        costs: CostRepository,
        cost_system: CostSystem,
        clock: Clock,
        logger: logging.Logger,
        batch_size: int,
        days_per_rental: int = 31,
        mileage: MileageSource | None = None,
    ) -> None:
        self._mileage_source = mileage
        self._costs = costs
        self._cost_system = cost_system
        self._clock = clock
        self._logger = logger
        self._batch_size = batch_size
        self._days_per_rental = days_per_rental

    async def run_once(self) -> int:
        now = self._clock.now()
        created = 0
        for candidate in await self._costs.accrual_candidates(now, self._batch_size):
            created += await self._accrue(candidate, now)
        return created

    async def _accrue(self, candidate: AccrualCandidate, now: datetime) -> int:
        last_day = candidate.last_accrued_day
        created = 0
        for period in due_periods(
            candidate.starts_at, candidate.returned_at, now, last_day, self._days_per_rental
        ):
            if not await self._accrue_period(candidate, period):
                return created
            created += 1
            last_day = period.day
        await self._complete_if_done(candidate, last_day)
        return created

    async def _accrue_period(self, candidate: AccrualCandidate, period: AccrualPeriod) -> bool:
        context = self._context(candidate, period)
        mileage: Decimal | None = None
        if candidate.spec.fuel_consumption_mode is FuelConsumptionMode.PER_100KM:
            mileage = await self._mileage(candidate, period, context)
            if mileage is None:
                return False
        try:
            calculation = await self._cost_system.calculate(
                CostCalculationRequest(
                    rental_id=candidate.rental_id,
                    external_reference=candidate.external_reference,
                    period=period,
                    shift_hours_per_day=candidate.shift_hours_per_day,
                    spec=candidate.spec,
                    mileage_km=mileage,
                )
            )
        except CostSystemUnavailableError as exc:
            self._logger.warning("Cost calculation postponed", extra={**context, "error": str(exc)})
            return False
        except CostSystemRejectedError as exc:
            self._logger.error("Cost calculation rejected", extra={**context, "error": str(exc)})
            return False
        if calculation.currency != candidate.spec.currency:
            self._logger.error(
                "Cost calculation in another currency ignored",
                extra={**context, "currency": calculation.currency},
            )
            return False
        stored = await self._costs.save_accrual(candidate.rental_id, period, calculation)
        self._logger.info(
            "Costs accrued" if stored else "Costs already accrued",
            extra={**context, "lines": len(calculation.lines)},
        )
        return True

    async def _mileage(
        self, candidate: AccrualCandidate, period: AccrualPeriod, context: dict[str, Any]
    ) -> Decimal | None:
        """Km of the period; ``None`` postpones the day."""
        object_id = candidate.spec.telematics_object_id
        if object_id is None:
            returned, start, end = (
                candidate.returned_at, candidate.odometer_start, candidate.odometer_end
            )
            if returned is None or start is None or end is None:
                self._logger.warning("Odometer readings missing", extra=context)
                return None
            return odometer_share(period, candidate.starts_at, returned, start, end)
        if self._mileage_source is None:
            self._logger.warning("Mileage from telematics is not available", extra=context)
            return None
        try:
            return await self._mileage_source.distance_km(
                candidate.organization_id, object_id, period.start, period.end
            )
        except TelematicsUnavailableError as exc:
            self._logger.warning("Mileage postponed", extra={**context, "error": str(exc)})
        except (TelematicsNotConfiguredError, TelematicsRejectedError) as exc:
            self._logger.error("Mileage unavailable", extra={**context, "error": str(exc)})
        return None

    async def _complete_if_done(self, candidate: AccrualCandidate, last_day: date | None) -> None:
        if accruals_complete(candidate.starts_at, candidate.returned_at, last_day):
            await self._costs.mark_complete(candidate.rental_id)
            self._logger.info(
                "Rental costs complete",
                extra={"operation": "accrue_costs", "aggregate_id": str(candidate.rental_id)},
            )

    @staticmethod
    def _context(candidate: AccrualCandidate, period: AccrualPeriod) -> dict[str, Any]:
        return {
            "operation": "accrue_costs",
            "organization_id": candidate.organization_id,
            "aggregate_id": str(candidate.rental_id),
            "accrual_date": period.day.isoformat(),
        }
