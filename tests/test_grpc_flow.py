"""The whole service in one process: gRPC API, PostgreSQL, workers and the stub."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from decimal import Decimal

import aiohttp
import asyncpg
import grpc
import pytest
from aiohttp.test_utils import TestServer
from google.protobuf.timestamp_pb2 import Timestamp

from src.application.equipment.accrual_service import AccrualService
from src.application.equipment.equipment_service import EquipmentService
from src.application.equipment.rental_service import RentalService
from src.application.equipment.submission_service import SubmissionService
from src.cost_system_stub.app import CostSystemStub
from src.cost_system_stub.calculator import CostCalculator
from src.cost_system_stub.settings import StubSettings, parse_prices
from src.domains.equipment import equipment_pb2 as pb2
from src.domains.equipment import equipment_pb2_grpc as pb2_grpc
from src.entrypoints.gRPC.equipment import EquipmentGrpcService, RentalGrpcService
from src.entrypoints.gRPC.requests import RequestMapper
from src.etc.settings_microservice.base import CostSystemSettings
from src.infrastructure.cost_system.client import HttpCostSystem
from src.infrastructure.db.repositories.costs import PostgresCostRepository
from src.infrastructure.db.repositories.equipment import PostgresEquipmentRepository
from src.infrastructure.db.repositories.outbox import PostgresOutboxRepository
from src.infrastructure.db.repositories.rentals import PostgresRentalRepository
from src.infrastructure.grpc.exceptions.base import ExceptionStatusMapper
from src.infrastructure.grpc.interceptors.base import ErrorInterceptor
from tests.common import LOGGER, FixedClock, at

pytestmark = pytest.mark.integration


@dataclass
class Harness:
    equipment: pb2_grpc.EquipmentServiceStub
    rentals: pb2_grpc.RentalServiceStub
    clock: FixedClock
    submission: SubmissionService
    accrual: AccrualService


def ts(value: object) -> Timestamp:
    stamp = Timestamp()
    stamp.FromDatetime(value)  # type: ignore[arg-type]
    return stamp


@pytest.fixture
async def harness(pool: asyncpg.Pool) -> AsyncIterator[Harness]:
    prices = parse_prices("diesel=68.50")
    stub = CostSystemStub(CostCalculator(prices), StubSettings(0, prices, 0.0, "stub", None), LOGGER)
    clock = FixedClock(at(1))
    async with TestServer(stub.application()) as stub_server, aiohttp.ClientSession() as session:
        url = str(stub_server.make_url("")).rstrip("/")
        cost_system = HttpCostSystem(CostSystemSettings(url, None, 5.0), session, LOGGER)
        server = grpc.aio.server(interceptors=[ErrorInterceptor(LOGGER, ExceptionStatusMapper())])
        requests = RequestMapper(Decimal(8))
        pb2_grpc.add_EquipmentServiceServicer_to_server(
            EquipmentGrpcService(EquipmentService(PostgresEquipmentRepository(pool), LOGGER), requests),
            server,
        )
        rentals = RentalService(PostgresRentalRepository(pool), PostgresCostRepository(pool), clock, LOGGER)
        pb2_grpc.add_RentalServiceServicer_to_server(RentalGrpcService(rentals, requests), server)
        port = server.add_insecure_port("127.0.0.1:0")
        await server.start()
        async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            yield Harness(
                pb2_grpc.EquipmentServiceStub(channel),
                pb2_grpc.RentalServiceStub(channel),
                clock,
                SubmissionService(PostgresOutboxRepository(pool), cost_system, clock, LOGGER, 10, 5),
                AccrualService(PostgresCostRepository(pool), cost_system, clock, LOGGER, 10),
            )
        await server.stop(None)


def tractor(**overrides: object) -> pb2.CreateEquipmentRequest:
    values: dict[str, object] = dict(
        organizationId=15, inventoryNumber="TR-001", name="Трактор МТЗ-82",
        purchasePrice="3650000.00", residualValue="350000.00", usefulLifeMonths=120,
        fuelType="diesel", fuelConsumptionPerHour="9.5", dailyOverhead="1200.00",
        currency="RUB", createdByUserId=3,
    )
    values.update(overrides)
    return pb2.CreateEquipmentRequest(**values)  # type: ignore[arg-type]


async def test_rental_costs_accrue_day_by_day(harness: Harness) -> None:
    equipment = (await harness.equipment.CreateEquipment(tractor())).data
    rental = (await harness.rentals.CreateRental(pb2.CreateRentalRequest(
        organizationId=15, equipmentId=equipment.id, workerUserId=7,
        startsAt=ts(at(1, 8)), endsAt=ts(at(5, 18)), purpose="Вспашка", createdByUserId=3,
    ))).data
    assert (rental.status, rental.externalStatus, rental.shiftHoursPerDay) == ("open", "pending", "8.00")

    assert await harness.submission.run_once() == 1
    harness.clock.current = at(3, 10)
    assert await harness.accrual.run_once() == 2

    harness.clock.current = at(3, 12)
    returned = (await harness.rentals.ReturnEquipment(pb2.ReturnEquipmentRequest(
        organizationId=15, id=rental.id, returnedAt=ts(at(3, 9, 30)), actorUserId=7,
    ))).data
    assert returned.status == "returned" and returned.externalReference.startswith("EXT-")
    assert await harness.submission.run_once() == 1
    assert await harness.accrual.run_once() == 1

    costs = (await harness.rentals.GetRentalCosts(
        pb2.GetRentalCostsRequest(organizationId=15, rentalId=rental.id))).data
    assert costs.complete and costs.currency == "RUB"
    assert [accrual.accrualDate for accrual in costs.accruals] == ["2026-10-01", "2026-10-02", "2026-10-03"]
    assert costs.accruals[1].total == "7309.49"  # a full day: 5206.00 + 903.49 + 1200.00
    assert Decimal(costs.total) == sum(Decimal(item.total) for item in costs.accruals)
    assert {total.costType for total in costs.totalsByType} == {"fuel", "depreciation", "overhead"}


async def test_errors_become_grpc_statuses(harness: Harness) -> None:
    equipment = (await harness.equipment.CreateEquipment(tractor())).data
    request = pb2.CreateRentalRequest(
        organizationId=15, equipmentId=equipment.id, workerUserId=7,
        startsAt=ts(at(2)), endsAt=ts(at(4)), createdByUserId=3,
    )
    await harness.rentals.CreateRental(request)

    cases = [
        (harness.rentals.CreateRental(request), grpc.StatusCode.FAILED_PRECONDITION),
        (harness.equipment.GetEquipment(pb2.GetEquipmentRequest(organizationId=16, id=equipment.id)),
         grpc.StatusCode.NOT_FOUND),
        (harness.equipment.CreateEquipment(tractor()), grpc.StatusCode.ALREADY_EXISTS),
        (harness.equipment.CreateEquipment(tractor(inventoryNumber="TR-2", purchasePrice="много")),
         grpc.StatusCode.INVALID_ARGUMENT),
        (harness.equipment.UpdateEquipment(pb2.UpdateEquipmentRequest(
            organizationId=15, id=equipment.id, name="x", expectedVersion=7)), grpc.StatusCode.ABORTED),
        (harness.rentals.GetRental(pb2.GetRentalRequest(organizationId=15, id="not-a-uuid")),
         grpc.StatusCode.INVALID_ARGUMENT),
    ]
    for call, status in cases:
        with pytest.raises(grpc.aio.AioRpcError) as caught:
            await call
        assert caught.value.code() == status, caught.value.details()
