from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from src.domain.equipment.models import (
    EquipmentSpec,
    EquipmentStatus,
    FuelConsumptionMode,
    FuelType,
    RentalStatus,
)
from src.domain.equipment.periods import AccrualPeriod


@dataclass(frozen=True, slots=True)
class EquipmentChanges:
    """Fields of an update; ``None`` keeps the stored value."""

    name: str | None = None
    catalog_item_id: str | None = None
    purchase_price: Decimal | None = None
    residual_value: Decimal | None = None
    useful_life_months: int | None = None
    fuel_type: FuelType | None = None
    fuel_consumption_per_hour: Decimal | None = None
    daily_overhead: Decimal | None = None
    status: EquipmentStatus | None = None
    registration_number: str | None = None
    vehicle_type_id: int | None = None
    vehicle_type_name: str | None = None
    fuel_consumption_mode: FuelConsumptionMode | None = None
    fuel_consumption_per_100km: Decimal | None = None
    telematics_object_id: int | None = None

    def spec_changes(self) -> dict[str, Any]:
        """Changed spec fields. Empty ``catalog_item_id`` or ``registration_number`` and a
        zero ``vehicle_type_id`` or ``telematics_object_id`` remove the value."""
        changes: dict[str, Any] = {
            name: value
            for name, value in (
                ("name", self.name),
                ("catalog_item_id", self.catalog_item_id),
                ("purchase_price", self.purchase_price),
                ("residual_value", self.residual_value),
                ("useful_life_months", self.useful_life_months),
                ("fuel_type", self.fuel_type),
                ("fuel_consumption_per_hour", self.fuel_consumption_per_hour),
                ("daily_overhead", self.daily_overhead),
                ("registration_number", self.registration_number),
                ("vehicle_type_id", self.vehicle_type_id),
                ("vehicle_type_name", self.vehicle_type_name),
                ("fuel_consumption_mode", self.fuel_consumption_mode),
                ("fuel_consumption_per_100km", self.fuel_consumption_per_100km),
                ("telematics_object_id", self.telematics_object_id),
            )
            if value is not None
        }
        for name, empty in (
            ("catalog_item_id", ""),
            ("registration_number", ""),
            ("vehicle_type_id", 0),
            ("telematics_object_id", 0),
        ):
            if changes.get(name) == empty:
                changes[name] = None
        if "vehicle_type_id" in changes and changes["vehicle_type_id"] is None:
            changes["vehicle_type_name"] = ""
        return changes


@dataclass(frozen=True, slots=True)
class ListQuery:
    organization_id: int
    limit: int
    offset: int


@dataclass(frozen=True, slots=True)
class RentalFilter:
    organization_id: int
    equipment_id: UUID | None
    worker_user_id: int | None
    status: RentalStatus | None
    limit: int
    offset: int


class OutboxKind(StrEnum):
    RENTAL_OPENED = "rental_opened"
    RENTAL_RETURNED = "rental_returned"
    RENTAL_CANCELLED = "rental_cancelled"


@dataclass(frozen=True, slots=True)
class OutboxMessage:
    id: UUID
    rental_id: UUID
    kind: OutboxKind
    payload: dict[str, Any]
    attempts: int


@dataclass(frozen=True, slots=True)
class AccrualCandidate:
    """A rental with days that may be due, and what the cost system needs about it."""

    rental_id: UUID
    organization_id: int
    starts_at: datetime
    returned_at: datetime | None
    shift_hours_per_day: Decimal
    external_reference: str | None
    last_accrued_day: date | None
    spec: EquipmentSpec
    odometer_start: Decimal | None = None
    odometer_end: Decimal | None = None


@dataclass(frozen=True, slots=True)
class CostCalculationRequest:
    rental_id: UUID
    external_reference: str | None
    period: AccrualPeriod
    shift_hours_per_day: Decimal
    spec: EquipmentSpec
    # Km driven in the period, for per_100km equipment.
    mileage_km: Decimal | None = None


@dataclass(frozen=True, slots=True)
class CalculatedLine:
    cost_type: str
    amount: Decimal
    quantity: Decimal | None
    unit: str | None
    description: str


@dataclass(frozen=True, slots=True)
class CostCalculation:
    calculation_id: str | None
    currency: str
    lines: tuple[CalculatedLine, ...]
