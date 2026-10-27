"""Stand-in cost formulas until the real cost system is connected.

fuel          = working hours x consumption per hour x fuel price, or, for equipment
                counted per 100 km, km driven x consumption per 100 km / 100 x price
depreciation  = (purchase - residual) / useful life, straight line per day
overhead      = daily overhead
Working hours are the shift hours per day scaled to the length of the period.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

DAYS_PER_MONTH = Decimal("30.4375")
CENT = Decimal("0.01")
UNITS = {"electric": "kWh"}


@dataclass(frozen=True, slots=True)
class StubLine:
    cost_type: str
    amount: Decimal
    quantity: Decimal | None
    unit: str | None
    description: str

    def to_json(self) -> dict[str, Any]:
        return {
            "costType": self.cost_type,
            "amount": str(self.amount),
            "quantity": str(self.quantity) if self.quantity is not None else None,
            "unit": self.unit,
            "description": self.description,
        }


class CostCalculator:
    def __init__(self, fuel_prices: dict[str, Decimal]) -> None:
        self._fuel_prices = fuel_prices

    def calculate(
        self,
        hours: Decimal,
        shift_hours: Decimal,
        equipment: dict[str, Any],
        mileage_km: Decimal | None = None,
    ) -> list[StubLine]:
        day_share = hours / Decimal(24)
        lines = [self._depreciation(day_share, equipment), self._overhead(day_share, equipment)]
        fuel = self._fuel(day_share * shift_hours, mileage_km, equipment)
        return ([fuel] if fuel else []) + lines

    def _fuel(
        self, working_hours: Decimal, mileage_km: Decimal | None, equipment: dict[str, Any]
    ) -> StubLine | None:
        fuel_type = str(equipment["fuelType"])
        if fuel_type == "none":
            return None
        price = self._fuel_prices.get(fuel_type)
        if price is None:
            raise ValueError(f"No price for fuel type {fuel_type!r}")
        unit = UNITS.get(fuel_type, "l")
        if equipment.get("fuelConsumptionMode") == "per_100km":
            if mileage_km is None:
                raise ValueError("mileageKm is required for per_100km equipment")
            per_100km = Decimal(equipment["fuelConsumptionPer100Km"])
            quantity = _litres(mileage_km * per_100km / Decimal(100))
            basis = f"{mileage_km} км x {per_100km}/100 км"
        else:
            quantity = _litres(working_hours * Decimal(equipment["fuelConsumptionPerHour"]))
            basis = f"{working_hours.quantize(CENT)} моточасов"
        return StubLine(
            "fuel", _money(quantity * price), quantity, unit, f"{fuel_type}: {basis} x {price}/{unit}"
        )

    @staticmethod
    def _depreciation(day_share: Decimal, equipment: dict[str, Any]) -> StubLine:
        base = Decimal(equipment["purchasePrice"]) - Decimal(equipment["residualValue"])
        per_day = base / Decimal(int(equipment["usefulLifeMonths"])) / DAYS_PER_MONTH
        return StubLine(
            "depreciation", _money(per_day * day_share), None, None, "Линейная амортизация"
        )

    @staticmethod
    def _overhead(day_share: Decimal, equipment: dict[str, Any]) -> StubLine:
        amount = Decimal(equipment["dailyOverhead"]) * day_share
        return StubLine(
            "overhead", _money(amount), None, None, "Страховка, хранение, ТО"
        )


def _litres(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.001"), ROUND_HALF_UP)


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, ROUND_HALF_UP)
