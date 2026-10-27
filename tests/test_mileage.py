"""Fuel by mileage: odometer readings, the stub's formula and the whole flow over gRPC."""

from __future__ import annotations

from decimal import Decimal

import grpc
import pytest

from src.cost_system_stub.calculator import CostCalculator
from src.cost_system_stub.settings import parse_prices
from src.domain.equipment.mileage import odometer_share
from src.domain.equipment.periods import AccrualPeriod
from src.domains.equipment import equipment_pb2 as pb2
from tests.common import at
from tests.test_grpc_flow import Harness, harness, tractor, ts  # noqa: F401 - fixture

DUMP_TRUCK = dict(
    inventoryNumber="DT-001", name="Самосвал КАМАЗ-65115", fuelConsumptionMode="per_100km",
    fuelConsumptionPer100Km="32", fuelConsumptionPerHour="0", registrationNumber=" а 123 вс 77 ",
)


def test_odometer_km_are_spread_over_the_rental_by_time() -> None:
    period = AccrualPeriod(at(2).date(), at(2), at(3))

    share = odometer_share(period, at(1, 12), at(3, 12), Decimal("1000"), Decimal("1300"))

    assert share == Decimal("150.0")


def test_stub_counts_fuel_per_100_km() -> None:
    calculator = CostCalculator(parse_prices("diesel=68.50"))
    equipment = {
        "purchasePrice": "0", "residualValue": "0", "usefulLifeMonths": 1, "fuelType": "diesel",
        "fuelConsumptionPerHour": "9.5", "fuelConsumptionMode": "per_100km",
        "fuelConsumptionPer100Km": "32", "dailyOverhead": "0",
    }

    fuel = calculator.calculate(Decimal(24), Decimal(8), equipment, Decimal("150"))[0]

    assert (fuel.cost_type, fuel.quantity, fuel.amount) == ("fuel", Decimal("48.000"), Decimal("3288.00"))
    with pytest.raises(ValueError):
        calculator.calculate(Decimal(24), Decimal(8), equipment)


async def test_plate_is_normalized_unique_and_removable(harness: Harness) -> None:
    truck = (await harness.equipment.CreateEquipment(tractor(**DUMP_TRUCK))).data
    assert truck.registrationNumber == "А 123 ВС 77"
    assert (truck.fuelConsumptionMode, truck.fuelConsumptionPer100Km) == ("per_100km", "32.000")

    with pytest.raises(grpc.aio.AioRpcError) as caught:
        await harness.equipment.CreateEquipment(tractor(**{**DUMP_TRUCK, "inventoryNumber": "DT-2"}))
    assert caught.value.code() == grpc.StatusCode.ALREADY_EXISTS

    cleared = (await harness.equipment.UpdateEquipment(pb2.UpdateEquipmentRequest(
        organizationId=15, id=truck.id, registrationNumber="", vehicleTypeId=7,
        vehicleTypeName="Самосвалы",
    ))).data
    assert (cleared.registrationNumber, cleared.vehicleTypeId, cleared.vehicleTypeName) == (
        "", 7, "Самосвалы",
    )


async def test_fuel_by_odometer_accrues_once_the_truck_is_back(harness: Harness) -> None:
    truck = (await harness.equipment.CreateEquipment(tractor(**DUMP_TRUCK))).data
    request = pb2.CreateRentalRequest(
        organizationId=15, equipmentId=truck.id, workerUserId=7, startsAt=ts(at(1, 12)),
        endsAt=ts(at(5)), createdByUserId=3,
    )
    with pytest.raises(grpc.aio.AioRpcError) as caught:
        await harness.rentals.CreateRental(request)
    assert caught.value.code() == grpc.StatusCode.INVALID_ARGUMENT

    request.odometerStart = "1000"
    rental = (await harness.rentals.CreateRental(request)).data
    assert rental.odometerStart == "1000"
    assert await harness.submission.run_once() == 1
    harness.clock.current = at(3, 18)
    assert await harness.accrual.run_once() == 0  # mileage unknown until the return

    back = pb2.ReturnEquipmentRequest(
        organizationId=15, id=rental.id, returnedAt=ts(at(3, 12)), actorUserId=7,
    )
    for odometer, status in (("", grpc.StatusCode.INVALID_ARGUMENT),
                             ("900", grpc.StatusCode.INVALID_ARGUMENT)):
        back.odometerEnd = odometer
        with pytest.raises(grpc.aio.AioRpcError) as caught:
            await harness.rentals.ReturnEquipment(back)
        assert caught.value.code() == status
    back.odometerEnd = "1300"
    assert (await harness.rentals.ReturnEquipment(back)).data.odometerEnd == "1300"
    await harness.submission.run_once()
    assert await harness.accrual.run_once() == 3

    costs = (await harness.rentals.GetRentalCosts(
        pb2.GetRentalCostsRequest(organizationId=15, rentalId=rental.id))).data
    litres = [
        Decimal(line.quantity) for accrual in costs.accruals for line in accrual.lines
        if line.costType == "fuel"
    ]
    assert litres == [Decimal("24"), Decimal("48"), Decimal("24")]  # 75 + 150 + 75 km x 32/100
