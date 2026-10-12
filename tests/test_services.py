from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from src.application.equipment.accrual_service import AccrualService
from src.application.equipment.commands import (
    AccrualCandidate,
    CalculatedLine,
    CostCalculation,
    CostCalculationRequest,
    OutboxKind,
    OutboxMessage,
)
from src.application.equipment.errors import CostSystemRejectedError, CostSystemUnavailableError
from src.application.equipment.rental_service import RentalService
from src.application.equipment.submission_service import SubmissionService
from src.domain.equipment.errors import InvalidDataError
from src.domain.equipment.periods import AccrualPeriod
from src.entrypoints.gRPC.responses import amount, quantity
from tests.common import LOGGER, FixedClock, at, make_spec


@dataclass
class FakeCostSystem:
    failures: list[Exception] = field(default_factory=list)
    currency: str = "RUB"
    calls: list[str] = field(default_factory=list)

    async def _answer(self, name: str) -> dict[str, Any]:
        self.calls.append(name)
        if self.failures:
            raise self.failures.pop(0)
        return {"reference": "EXT-1"}

    async def register_rental(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._answer("register")

    async def notify_return(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._answer("return")

    async def notify_cancel(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._answer("cancel")

    async def calculate(self, request: CostCalculationRequest) -> CostCalculation:
        await self._answer(f"calc:{request.period.day.isoformat()}")
        return CostCalculation(
            "c", self.currency, (CalculatedLine("fuel", Decimal("10"), None, None, ""),)
        )


@dataclass
class FakeOutbox:
    messages: list[OutboxMessage]
    sent: list[tuple[OutboxMessage, dict[str, Any]]] = field(default_factory=list)
    retried: list[datetime] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    async def claim(self, now: datetime, limit: int) -> list[OutboxMessage]:
        return self.messages[:limit]

    async def mark_sent(self, message: OutboxMessage, result: dict[str, Any]) -> None:
        self.sent.append((message, result))

    async def retry_later(self, message: OutboxMessage, error: str, next_attempt_at: datetime) -> None:
        self.retried.append(next_attempt_at)

    async def mark_failed(self, message: OutboxMessage, error: str) -> None:
        self.failed.append(error)


@dataclass
class FakeCosts:
    candidates: list[AccrualCandidate]
    saved: list[AccrualPeriod] = field(default_factory=list)
    completed: list[Any] = field(default_factory=list)

    async def accrual_candidates(self, now: datetime, limit: int) -> list[AccrualCandidate]:
        return self.candidates

    async def save_accrual(self, rental_id: Any, period: AccrualPeriod, calculation: CostCalculation) -> bool:
        self.saved.append(period)
        return True

    async def mark_complete(self, rental_id: Any) -> None:
        self.completed.append(rental_id)

    async def costs(self, organization_id: int, rental_id: Any) -> Any:
        raise NotImplementedError


def message(attempts: int = 0, kind: OutboxKind = OutboxKind.RENTAL_OPENED) -> OutboxMessage:
    return OutboxMessage(uuid4(), uuid4(), kind, {"rentalId": "r"}, attempts)


def submission(outbox: FakeOutbox, system: FakeCostSystem, max_attempts: int = 3) -> SubmissionService:
    return SubmissionService(outbox, system, FixedClock(at(1)), LOGGER, 10, max_attempts)


def candidate(returned_at: datetime | None = None, last_day: date | None = None) -> AccrualCandidate:
    return AccrualCandidate(uuid4(), 15, at(1, 8), returned_at, Decimal("8"), "EXT-1", last_day, make_spec())


async def test_delivered_messages_are_marked_sent() -> None:
    outbox = FakeOutbox([message(kind=OutboxKind.RENTAL_OPENED), message(kind=OutboxKind.RENTAL_CANCELLED)])
    system = FakeCostSystem()

    assert await submission(outbox, system).run_once() == 2
    assert system.calls == ["register", "cancel"]
    assert outbox.sent[0][1] == {"reference": "EXT-1"}


async def test_unavailable_system_is_retried_with_backoff_then_given_up() -> None:
    outbox = FakeOutbox([message(attempts=0), message(attempts=1), message(attempts=2)])
    system = FakeCostSystem(failures=[CostSystemUnavailableError("503")] * 3)

    await submission(outbox, system, max_attempts=3).run_once()

    assert outbox.retried == [at(1) + timedelta(seconds=5), at(1) + timedelta(seconds=10)]
    assert outbox.failed == ["503"]


async def test_rejection_fails_at_once() -> None:
    outbox = FakeOutbox([message()])

    await submission(outbox, FakeCostSystem(failures=[CostSystemRejectedError("400")])).run_once()

    assert outbox.failed == ["400"] and outbox.retried == []


@dataclass
class FlakyCostSystem(FakeCostSystem):
    """Answers the first calculation, then is down."""

    async def calculate(self, request: CostCalculationRequest) -> CostCalculation:
        if any(call.startswith("calc:") for call in self.calls):
            raise CostSystemUnavailableError("down")
        return await super().calculate(request)


async def test_accrual_stops_at_first_unavailable_day() -> None:
    costs = FakeCosts([candidate()])
    service = AccrualService(costs, FlakyCostSystem(), FixedClock(at(4, 10)), LOGGER, 10)

    assert await service.run_once() == 1
    assert [period.day for period in costs.saved] == [date(2026, 10, 1)]
    assert costs.completed == []


async def test_returned_rental_is_completed_after_last_day() -> None:
    costs = FakeCosts([candidate(returned_at=at(3, 9, 30))])
    service = AccrualService(costs, FakeCostSystem(), FixedClock(at(5)), LOGGER, 10)

    assert await service.run_once() == 3
    assert costs.saved[-1].end == at(3, 9, 30)
    assert len(costs.completed) == 1


async def test_calculation_in_another_currency_is_not_stored() -> None:
    costs = FakeCosts([candidate()])
    service = AccrualService(costs, FakeCostSystem(currency="USD"), FixedClock(at(3)), LOGGER, 10)

    assert await service.run_once() == 0
    assert costs.saved == []


async def test_return_time_in_future_is_rejected() -> None:
    service = RentalService(None, None, FixedClock(at(3)), LOGGER)  # type: ignore[arg-type]

    with pytest.raises(InvalidDataError):
        await service.return_equipment(15, uuid4(), at(3, 1), actor_user_id=7)


def test_amounts_are_shown_with_meaningful_decimals() -> None:
    assert [amount(Decimal(raw)) for raw in ("5206.0000", "903.4900", "0.1250", "12", "0E-4")] == [
        "5206.00", "903.49", "0.125", "12.00", "0.00"]
    assert quantity(Decimal("76.0000")) == "76"
