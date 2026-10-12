"""Protobuf requests into domain values; malformed input becomes InvalidDataError."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, TypeVar
from uuid import UUID

from google.protobuf.message import Message

from src.application.equipment.commands import EquipmentChanges, ListQuery, RentalFilter
from src.domain.equipment.errors import InvalidDataError
from src.domain.equipment.models import (
    EquipmentSpec,
    EquipmentStatus,
    FuelType,
    RentalRequest,
    RentalStatus,
)
from src.domains.equipment import equipment_pb2 as pb2

E = TypeVar("E", bound=StrEnum)

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


class RequestMapper:
    def __init__(self, default_shift_hours: Decimal) -> None:
        self._default_shift_hours = default_shift_hours

    def spec(self, request: pb2.CreateEquipmentRequest) -> EquipmentSpec:
        return EquipmentSpec(
            organization_id=self.organization(request.organizationId),
            inventory_number=request.inventoryNumber.strip(),
            name=request.name.strip(),
            catalog_item_id=request.catalogItemId or None,
            purchase_price=decimal(request.purchasePrice, "purchasePrice"),
            residual_value=decimal(request.residualValue or "0", "residualValue"),
            useful_life_months=request.usefulLifeMonths,
            fuel_type=enum(FuelType, request.fuelType or "none", "fuelType"),
            fuel_consumption_per_hour=decimal(
                request.fuelConsumptionPerHour or "0", "fuelConsumptionPerHour"
            ),
            daily_overhead=decimal(request.dailyOverhead or "0", "dailyOverhead"),
            currency=(request.currency or "RUB").upper(),
        )

    def changes(self, request: pb2.UpdateEquipmentRequest) -> EquipmentChanges:
        message: Message = request

        def present(name: str) -> Any:
            return getattr(request, name) if message.HasField(name) else None

        decimals = {
            name: decimal(present(name), name) if present(name) is not None else None
            for name in ("purchasePrice", "residualValue", "fuelConsumptionPerHour", "dailyOverhead")
        }
        return EquipmentChanges(
            name=present("name"),
            catalog_item_id=present("catalogItemId"),
            purchase_price=decimals["purchasePrice"],
            residual_value=decimals["residualValue"],
            useful_life_months=present("usefulLifeMonths"),
            fuel_type=enum(FuelType, present("fuelType"), "fuelType") if present("fuelType") else None,
            fuel_consumption_per_hour=decimals["fuelConsumptionPerHour"],
            daily_overhead=decimals["dailyOverhead"],
            status=enum(EquipmentStatus, present("status"), "status") if present("status") else None,
        )

    def rental(self, request: pb2.CreateRentalRequest) -> RentalRequest:
        shift = request.shiftHoursPerDay if request.HasField("shiftHoursPerDay") else None
        return RentalRequest(
            organization_id=self.organization(request.organizationId),
            equipment_id=identifier(request.equipmentId, "equipmentId"),
            worker_user_id=request.workerUserId,
            starts_at=timestamp(request, "startsAt"),
            ends_at=timestamp(request, "endsAt"),
            shift_hours_per_day=decimal(shift, "shiftHoursPerDay") if shift else self._default_shift_hours,
            purpose=request.purpose.strip(),
            created_by_user_id=request.createdByUserId,
        )

    def rental_filter(self, request: pb2.ListRentalsRequest) -> RentalFilter:
        query = self.page(request.organizationId, request.limit, request.offset)
        return RentalFilter(
            organization_id=query.organization_id,
            equipment_id=identifier(request.equipmentId, "equipmentId")
            if request.HasField("equipmentId") else None,
            worker_user_id=request.workerUserId if request.HasField("workerUserId") else None,
            status=enum(RentalStatus, request.status, "status") if request.HasField("status") else None,
            limit=query.limit,
            offset=query.offset,
        )

    def page(self, organization_id: int, limit: int, offset: int) -> ListQuery:
        if limit < 0 or offset < 0:
            raise InvalidDataError("limit and offset must not be negative")
        return ListQuery(self.organization(organization_id), min(limit or DEFAULT_LIMIT, MAX_LIMIT), offset)

    @staticmethod
    def organization(value: int) -> int:
        if value < 1:
            raise InvalidDataError("organizationId is required")
        return value


def decimal(value: str | None, name: str) -> Decimal:
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise InvalidDataError(f"{name} must be a decimal number") from exc
    if not result.is_finite():
        raise InvalidDataError(f"{name} must be a finite number")
    return result


def identifier(value: str, name: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise InvalidDataError(f"{name} must be a UUID") from exc


def enum(kind: type[E], value: str | None, name: str) -> E:
    try:
        return kind(str(value))
    except ValueError as exc:
        allowed = ", ".join(item.value for item in kind)
        raise InvalidDataError(f"{name} must be one of: {allowed}") from exc


def timestamp(message: Message, name: str) -> datetime:
    value = optional_timestamp(message, name)
    if value is None:
        raise InvalidDataError(f"{name} is required")
    return value


def optional_timestamp(message: Message, name: str) -> datetime | None:
    if not message.HasField(name):
        return None
    value: datetime = getattr(message, name).ToDatetime(tzinfo=UTC)
    return value
