from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import ClassVar
from uuid import UUID

from src.domain.equipment.errors import InvalidDataError


class EquipmentStatus(StrEnum):
    AVAILABLE = "available"
    MAINTENANCE = "maintenance"
    DECOMMISSIONED = "decommissioned"


class FuelType(StrEnum):
    DIESEL = "diesel"
    PETROL = "petrol"
    GAS = "gas"
    ELECTRIC = "electric"
    NONE = "none"


class FuelConsumptionMode(StrEnum):
    """How fuel is counted: fixed litres per engine hour, or litres per 100 km driven."""

    PER_HOUR = "per_hour"
    PER_100KM = "per_100km"


class RentalStatus(StrEnum):
    OPEN = "open"
    RETURNED = "returned"
    CANCELLED = "cancelled"


class ExternalStatus(StrEnum):
    """Delivery of the rental to the external cost system."""

    PENDING = "pending"
    SUBMITTED = "submitted"
    FAILED = "failed"


def require_aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidDataError(f"{name} must include a time zone")
    return value


@dataclass(frozen=True, slots=True)
class EquipmentSpec:
    """Registry data of one unit of equipment, used by the cost calculation."""

    organization_id: int
    inventory_number: str
    name: str
    catalog_item_id: str | None
    purchase_price: Decimal
    residual_value: Decimal
    useful_life_months: int
    fuel_type: FuelType
    fuel_consumption_per_hour: Decimal
    daily_overhead: Decimal
    currency: str
    registration_number: str | None = None
    vehicle_type_id: int | None = None
    vehicle_type_name: str = ""
    fuel_consumption_mode: FuelConsumptionMode = FuelConsumptionMode.PER_HOUR
    fuel_consumption_per_100km: Decimal = Decimal(0)
    telematics_object_id: int | None = None

    _CURRENCY: ClassVar[re.Pattern[str]] = re.compile(r"^[A-Z]{3}$")
    # Russian plates and trailer/special machinery numbers: letters, digits, spaces, dashes.
    _PLATE: ClassVar[re.Pattern[str]] = re.compile(r"^[0-9A-ZА-ЯЁ][0-9A-ZА-ЯЁ -]{0,19}$")

    def __post_init__(self) -> None:
        if not self.inventory_number.strip() or not self.name.strip():
            raise InvalidDataError("inventoryNumber and name are required")
        if min(self.purchase_price, self.residual_value) < 0:
            raise InvalidDataError("Prices must not be negative")
        if self.residual_value > self.purchase_price:
            raise InvalidDataError("residualValue must not exceed purchasePrice")
        if self.useful_life_months < 1:
            raise InvalidDataError("usefulLifeMonths must be positive")
        if min(self.fuel_consumption_per_hour, self.daily_overhead) < 0:
            raise InvalidDataError("Fuel consumption and overhead must not be negative")
        if not self._CURRENCY.match(self.currency):
            raise InvalidDataError("currency must be an ISO 4217 code, e.g. RUB")
        if self.registration_number is not None and not self._PLATE.match(
            self.registration_number
        ):
            raise InvalidDataError("registrationNumber: up to 20 letters, digits, spaces, dashes")
        if self.vehicle_type_id is not None and self.vehicle_type_id < 1:
            raise InvalidDataError("vehicleTypeId must be positive")
        if len(self.vehicle_type_name) > 300:
            raise InvalidDataError("vehicleTypeName is too long")
        if self.fuel_consumption_per_100km < 0:
            raise InvalidDataError("fuelConsumptionPer100Km must not be negative")
        if self.telematics_object_id is not None and self.telematics_object_id < 1:
            raise InvalidDataError("telematicsObjectId must be positive")

    @property
    def mileage_from_odometer(self) -> bool:
        """Fuel by mileage, and no tracker to measure it: readings come with the rental."""
        return (
            self.fuel_consumption_mode is FuelConsumptionMode.PER_100KM
            and self.telematics_object_id is None
        )


def normalize_plate(value: str | None) -> str | None:
    """Upper case without repeated spaces; an empty number is no number."""
    if value is None:
        return None
    plate = " ".join(value.upper().split())
    return plate or None


@dataclass(frozen=True, slots=True)
class Equipment:
    id: UUID
    spec: EquipmentSpec
    status: EquipmentStatus
    version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class RentalRequest:
    organization_id: int
    equipment_id: UUID
    worker_user_id: int
    starts_at: datetime
    ends_at: datetime
    shift_hours_per_day: Decimal
    purpose: str
    created_by_user_id: int
    odometer_start: Decimal | None = None

    def __post_init__(self) -> None:
        require_aware(self.starts_at, "startsAt")
        require_aware(self.ends_at, "endsAt")
        if self.ends_at <= self.starts_at:
            raise InvalidDataError("endsAt must be after startsAt")
        if not Decimal(0) < self.shift_hours_per_day <= Decimal(24):
            raise InvalidDataError("shiftHoursPerDay must be within (0, 24]")
        if self.worker_user_id < 1:
            raise InvalidDataError("workerUserId is required")
        if len(self.purpose) > 1000:
            raise InvalidDataError("purpose is too long")
        require_odometer(self.odometer_start, "odometerStart")


def require_odometer(value: Decimal | None, name: str) -> None:
    if value is not None and not Decimal(0) <= value < Decimal(10) ** 9:
        raise InvalidDataError(f"{name} must be a reading in km")


@dataclass(frozen=True, slots=True)
class Rental:
    id: UUID
    organization_id: int
    equipment_id: UUID
    worker_user_id: int
    starts_at: datetime
    ends_at: datetime
    returned_at: datetime | None
    status: RentalStatus
    shift_hours_per_day: Decimal
    purpose: str
    created_by_user_id: int
    external_status: ExternalStatus
    external_reference: str | None
    created_at: datetime
    updated_at: datetime
    odometer_start: Decimal | None = None
    odometer_end: Decimal | None = None


@dataclass(frozen=True, slots=True)
class CostLine:
    cost_type: str
    amount: Decimal
    quantity: Decimal | None
    unit: str | None
    description: str


@dataclass(frozen=True, slots=True)
class CostAccrual:
    id: UUID
    rental_id: UUID
    accrual_date: date
    period_start: datetime
    period_end: datetime
    currency: str
    external_calculation_id: str | None
    calculated_at: datetime
    lines: tuple[CostLine, ...] = field(default_factory=tuple)

    @property
    def total(self) -> Decimal:
        return sum((line.amount for line in self.lines), Decimal(0))


@dataclass(frozen=True, slots=True)
class RentalCosts:
    rental_id: UUID
    currency: str
    accruals: tuple[CostAccrual, ...]
    complete: bool

    @property
    def total(self) -> Decimal:
        return sum((accrual.total for accrual in self.accruals), Decimal(0))

    def totals_by_type(self) -> dict[str, Decimal]:
        totals: dict[str, Decimal] = {}
        for accrual in self.accruals:
            for line in accrual.lines:
                totals[line.cost_type] = totals.get(line.cost_type, Decimal(0)) + line.amount
        return dict(sorted(totals.items()))
