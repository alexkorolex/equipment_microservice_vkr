"""Repositories against a real PostgreSQL: locks, constraints and the outbox queue."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import asyncpg
import pytest

from src.application.equipment.commands import (
    CalculatedLine,
    CostCalculation,
    EquipmentChanges,
    ListQuery,
    OutboxKind,
)
from src.domain.equipment.errors import (
    EquipmentUnavailableError,
    InvalidDataError,
    NotFoundError,
    RentalStateError,
    VersionConflictError,
)
from src.domain.equipment.models import EquipmentStatus, ExternalStatus, RentalStatus
from src.domain.equipment.periods import AccrualPeriod
from src.infrastructure.db.repositories.costs import PostgresCostRepository
from src.infrastructure.db.repositories.equipment import PostgresEquipmentRepository
from src.infrastructure.db.repositories.outbox import PostgresOutboxRepository
from src.infrastructure.db.repositories.rentals import PostgresRentalRepository
from tests.common import at, make_request, make_spec

pytestmark = pytest.mark.integration

CALCULATION = CostCalculation(
    calculation_id="calc-1",
    currency="RUB",
    lines=(
        CalculatedLine("fuel", Decimal("4826.00"), Decimal("76.0"), "l", "Дизель"),
        CalculatedLine("depreciation", Decimal("904.11"), None, None, "Линейная"),
    ),
)


async def test_equipment_is_scoped_versioned_and_validated(pool: asyncpg.Pool) -> None:
    repository = PostgresEquipmentRepository(pool)
    created = await repository.create(make_spec(), created_by_user_id=3)

    with pytest.raises(NotFoundError):
        await repository.get(99, created.id)
    updated = await repository.update(
        15, created.id, EquipmentChanges(name="МТЗ-82.1", status=EquipmentStatus.MAINTENANCE), 1
    )
    assert (updated.spec.name, updated.status, updated.version) == (
        "МТЗ-82.1", EquipmentStatus.MAINTENANCE, 2)
    with pytest.raises(VersionConflictError):
        await repository.update(15, created.id, EquipmentChanges(name="x"), expected_version=1)
    with pytest.raises(InvalidDataError):
        await repository.update(15, created.id, EquipmentChanges(residual_value=Decimal("9e9")), None)
    with pytest.raises(asyncpg.UniqueViolationError):
        await repository.create(make_spec(), created_by_user_id=3)
    listed = await repository.list(ListQuery(15, 10, 0), EquipmentStatus.MAINTENANCE)
    assert [item.id for item in listed] == [created.id]


async def test_open_rental_writes_outbox_and_blocks_overlaps(pool: asyncpg.Pool) -> None:
    equipment = await PostgresEquipmentRepository(pool).create(make_spec(), 3)
    rentals = PostgresRentalRepository(pool)

    rental = await rentals.open(make_request(equipment.id), now=at(1))

    assert rental.status is RentalStatus.OPEN and rental.external_status is ExternalStatus.PENDING
    [message] = await PostgresOutboxRepository(pool).claim(at(1), 10)
    assert message.kind is OutboxKind.RENTAL_OPENED
    assert message.payload["equipment"]["inventoryNumber"] == "TR-001"
    with pytest.raises(EquipmentUnavailableError):
        await rentals.open(make_request(equipment.id, starts_at=at(5), ends_at=at(7)), now=at(1))
    later = await rentals.open(make_request(equipment.id, starts_at=at(6), ends_at=at(8)), now=at(1))
    assert later.starts_at == at(6)


async def test_overdue_open_rental_still_holds_equipment(pool: asyncpg.Pool) -> None:
    equipment = await PostgresEquipmentRepository(pool).create(make_spec(), 3)
    rentals = PostgresRentalRepository(pool)
    await rentals.open(make_request(equipment.id, starts_at=at(1), ends_at=at(3)), now=at(1))

    with pytest.raises(EquipmentUnavailableError):
        await rentals.open(make_request(equipment.id, starts_at=at(4), ends_at=at(6)), now=at(5))


async def test_concurrent_overlapping_rentals_admit_exactly_one(pool: asyncpg.Pool) -> None:
    equipment = await PostgresEquipmentRepository(pool).create(make_spec(), 3)
    rentals = PostgresRentalRepository(pool)

    results = await asyncio.gather(
        *(rentals.open(make_request(equipment.id, worker_user_id=worker), now=at(1))
          for worker in range(1, 6)),
        return_exceptions=True,
    )

    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert all(isinstance(result, EquipmentUnavailableError)
               for result in results if isinstance(result, Exception))


async def test_equipment_out_of_service_cannot_be_rented(pool: asyncpg.Pool) -> None:
    repository = PostgresEquipmentRepository(pool)
    equipment = await repository.create(make_spec(), 3)
    await repository.update(15, equipment.id, EquipmentChanges(status=EquipmentStatus.MAINTENANCE), None)

    with pytest.raises(EquipmentUnavailableError):
        await PostgresRentalRepository(pool).open(make_request(equipment.id), now=at(1))


async def test_return_and_cancel_rules(pool: asyncpg.Pool) -> None:
    equipment = await PostgresEquipmentRepository(pool).create(make_spec(), 3)
    rentals = PostgresRentalRepository(pool)
    costs = PostgresCostRepository(pool)
    rental = await rentals.open(make_request(equipment.id), now=at(1))
    day = AccrualPeriod(date(2026, 10, 1), at(1, 8), at(2))
    await costs.save_accrual(rental.id, day, CALCULATION)

    with pytest.raises(RentalStateError):
        await rentals.cancel(15, rental.id, now=at(2))
    with pytest.raises(RentalStateError):
        await rentals.return_equipment(15, rental.id, at(1, 20), None)
    returned = await rentals.return_equipment(15, rental.id, at(3, 9), None)
    assert returned.status is RentalStatus.RETURNED and returned.returned_at == at(3, 9)
    with pytest.raises(RentalStateError):
        await rentals.return_equipment(15, rental.id, at(3, 10), None)

    future = await rentals.open(make_request(equipment.id, starts_at=at(10), ends_at=at(12)), at(3))
    cancelled = await rentals.cancel(15, future.id, now=at(3))
    assert cancelled.status is RentalStatus.CANCELLED


async def test_outbox_keeps_order_per_rental_and_leases(pool: asyncpg.Pool) -> None:
    equipment = await PostgresEquipmentRepository(pool).create(make_spec(), 3)
    rentals = PostgresRentalRepository(pool)
    outbox = PostgresOutboxRepository(pool, lease=timedelta(seconds=60))
    rental = await rentals.open(make_request(equipment.id), now=at(1))
    await rentals.return_equipment(15, rental.id, at(2), None)

    [opened] = await outbox.claim(at(3), 10)
    assert opened.kind is OutboxKind.RENTAL_OPENED
    assert await outbox.claim(at(3), 10) == []  # leased, and the return waits for it
    await outbox.mark_sent(opened, {"reference": "EXT-42"})
    stored = await rentals.get(15, rental.id)
    assert (stored.external_status, stored.external_reference) == (ExternalStatus.SUBMITTED, "EXT-42")

    [returned] = await outbox.claim(at(3), 10)
    assert returned.kind is OutboxKind.RENTAL_RETURNED
    await outbox.retry_later(returned, "down", next_attempt_at=at(4))
    assert await outbox.claim(at(3, 12), 10) == []
    [again] = await outbox.claim(at(4), 10)
    assert again.attempts == 1


async def test_failed_registration_marks_rental(pool: asyncpg.Pool) -> None:
    equipment = await PostgresEquipmentRepository(pool).create(make_spec(), 3)
    rental = await PostgresRentalRepository(pool).open(make_request(equipment.id), now=at(1))
    outbox = PostgresOutboxRepository(pool)

    [message] = await outbox.claim(at(1), 10)
    await outbox.mark_failed(message, "invalid inventory number")

    stored = await PostgresRentalRepository(pool).get(15, rental.id)
    assert stored.external_status is ExternalStatus.FAILED


async def test_accruals_are_idempotent_and_aggregated(pool: asyncpg.Pool) -> None:
    equipment = await PostgresEquipmentRepository(pool).create(make_spec(), 3)
    rentals = PostgresRentalRepository(pool)
    rental = await rentals.open(make_request(equipment.id), now=at(1))
    costs = PostgresCostRepository(pool)
    assert await costs.accrual_candidates(at(3), 10) == []  # not registered yet

    outbox = PostgresOutboxRepository(pool)
    [message] = await outbox.claim(at(1), 10)
    await outbox.mark_sent(message, {"reference": "EXT-1"})
    [candidate] = await costs.accrual_candidates(at(3), 10)
    assert candidate.last_accrued_day is None and candidate.spec.currency == "RUB"

    first = AccrualPeriod(date(2026, 10, 1), at(1, 8), at(2))
    assert await costs.save_accrual(rental.id, first, CALCULATION)
    assert not await costs.save_accrual(rental.id, first, CALCULATION)
    second = replace(first, day=date(2026, 10, 2), start=at(2), end=at(3))
    await costs.save_accrual(rental.id, second, CALCULATION)

    result = await costs.costs(15, rental.id)
    assert result.total == Decimal("11460.22")
    assert result.totals_by_type() == {"depreciation": Decimal("1808.22"), "fuel": Decimal("9652.00")}
    assert [line.cost_type for line in result.accruals[0].lines] == ["fuel", "depreciation"]
    [candidate] = await costs.accrual_candidates(at(3), 10)
    assert candidate.last_accrued_day == date(2026, 10, 2)
    await costs.mark_complete(rental.id)
    assert await costs.accrual_candidates(at(3), 10) == []
    with pytest.raises(NotFoundError):
        await costs.costs(16, rental.id)
