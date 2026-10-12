from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import uuid4

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from src.application.equipment.commands import CostCalculationRequest
from src.application.equipment.errors import CostSystemRejectedError, CostSystemUnavailableError
from src.cost_system_stub.app import CostSystemStub
from src.cost_system_stub.calculator import CostCalculator
from src.cost_system_stub.settings import StubSettings, parse_prices
from src.domain.equipment.models import FuelType
from src.domain.equipment.periods import AccrualPeriod
from src.etc.settings_microservice.base import CostSystemSettings
from src.infrastructure.cost_system.client import HttpCostSystem
from tests.common import LOGGER, at, make_spec

PRICES = parse_prices("diesel=68.50,electric=7.40")


def equipment_json(**overrides: Any) -> dict[str, Any]:
    return {
        "purchasePrice": "3650000.00", "residualValue": "350000.00", "usefulLifeMonths": 120,
        "fuelType": "diesel", "fuelConsumptionPerHour": "9.5", "dailyOverhead": "1200.00",
        "currency": "RUB", **overrides,
    }


def amounts(lines: list[Any]) -> dict[str, Decimal]:
    return {line.cost_type: line.amount for line in lines}


def test_full_day_by_formulas() -> None:
    lines = CostCalculator(PRICES).calculate(Decimal(24), Decimal(8), equipment_json())

    # fuel 8 h x 9.5 l x 68.50; depreciation 3 300 000 / 120 / 30.4375; overhead 1200
    assert amounts(lines) == {
        "fuel": Decimal("5206.00"),
        "depreciation": Decimal("903.49"),
        "overhead": Decimal("1200.00"),
    }
    assert lines[0].quantity == Decimal("76.000") and lines[0].unit == "l"


def test_partial_day_and_fuel_kinds() -> None:
    calculator = CostCalculator(PRICES)

    partial = amounts(calculator.calculate(Decimal(16), Decimal(8), equipment_json()))
    electric = calculator.calculate(Decimal(24), Decimal(8), equipment_json(fuelType="electric"))
    no_fuel = calculator.calculate(Decimal(24), Decimal(8), equipment_json(fuelType="none"))

    assert partial == {"fuel": Decimal("3470.69"), "depreciation": Decimal("602.33"),
                       "overhead": Decimal("800.00")}
    assert electric[0].unit == "kWh"
    assert [line.cost_type for line in no_fuel] == ["depreciation", "overhead"]
    with pytest.raises(ValueError):
        calculator.calculate(Decimal(24), Decimal(8), equipment_json(fuelType="petrol"))


def stub(failure_rate: float = 0.0) -> CostSystemStub:
    settings = StubSettings(8090, PRICES, failure_rate, "stub", None)
    return CostSystemStub(CostCalculator(PRICES), settings, LOGGER, chance=lambda: 0.5)


@pytest.fixture
async def stub_server() -> AsyncIterator[TestServer]:
    server = TestServer(stub().application())
    await server.start_server()
    yield server
    await server.close()


@pytest.fixture
async def client(stub_server: TestServer) -> AsyncIterator[HttpCostSystem]:
    settings = CostSystemSettings(str(stub_server.make_url("")).rstrip("/"), "secret", 5.0)
    async with aiohttp.ClientSession() as session:
        yield HttpCostSystem(settings, session, LOGGER)


def calculation_request(**spec: Any) -> CostCalculationRequest:
    period = AccrualPeriod(date(2026, 10, 1), at(1, 8), at(2))
    return CostCalculationRequest(uuid4(), "EXT-1", period, Decimal(8), make_spec(**spec))


async def test_client_talks_to_stub(client: HttpCostSystem) -> None:
    registered = await client.register_rental({
        "rentalId": "0f0e0d0c-0000-0000-0000-000000000000",
        "equipment": {"inventoryNumber": "TR-001"}, "workerUserId": 7,
    })
    calculation = await client.calculate(calculation_request())

    assert registered == {"reference": "EXT-0F0E0D0C"}
    assert calculation.currency == "RUB" and calculation.calculation_id
    assert {line.cost_type: line.amount for line in calculation.lines} == {
        "fuel": Decimal("3470.69"), "depreciation": Decimal("602.33"), "overhead": Decimal("800.00")}
    await client.notify_return({"rentalId": "0f0e0d0c-0000-0000-0000-000000000000"})


async def test_stub_rejects_unknown_fuel_price(client: HttpCostSystem) -> None:
    with pytest.raises(CostSystemRejectedError):
        await client.calculate(calculation_request(fuel_type=FuelType.GAS))


async def test_stub_simulates_outages() -> None:
    async with TestClient(TestServer(stub(failure_rate=1.0).application())) as http:
        assert (await http.post("/v1/rentals", json={})).status == 503
        assert (await http.get("/health")).status == 200


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (503, "", CostSystemUnavailableError),
        (429, "", CostSystemUnavailableError),
        (422, '{"error": "bad"}', CostSystemRejectedError),
        (200, "not json", CostSystemRejectedError),
        (200, '{"currency": "RUB", "lines": [{"costType": "fuel", "amount": "NaN"}]}',
         CostSystemRejectedError),
        (200, '{"lines": []}', CostSystemRejectedError),
    ],
)
async def test_client_classifies_answers(status: int, body: str, error: type[Exception]) -> None:
    seen: dict[str, str] = {}

    async def handler(request: web.Request) -> web.Response:
        seen.update(request.headers)
        return web.Response(status=status, text=body, content_type="application/json")

    app = web.Application()
    app.router.add_post("/v1/cost-calculations", handler)
    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        settings = CostSystemSettings(str(server.make_url("")).rstrip("/"), "secret", 5.0)
        with pytest.raises(error):
            await HttpCostSystem(settings, session, LOGGER).calculate(calculation_request())

    assert seen["Authorization"] == "Bearer secret"
    assert seen["Idempotency-Key"].endswith(":2026-10-01")


async def test_unreachable_system_is_unavailable() -> None:
    settings = CostSystemSettings("http://127.0.0.1:9", None, 1.0)
    async with aiohttp.ClientSession() as session:
        with pytest.raises(CostSystemUnavailableError):
            await HttpCostSystem(settings, session, LOGGER).register_rental({"rentalId": "x"})
