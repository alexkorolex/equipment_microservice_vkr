"""Domain objects into protobuf messages."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from google.protobuf.timestamp_pb2 import Timestamp

from src.domain.equipment.models import CostAccrual, Equipment, Rental, RentalCosts
from src.domains.equipment import equipment_pb2 as pb2


def ts(value: datetime) -> Timestamp:
    stamp = Timestamp()
    stamp.FromDatetime(value)
    return stamp


def amount(value: Decimal) -> str:
    """At least two decimals, no meaningless trailing zeros: 5206.0000 -> "5206.00"."""
    exponent = value.normalize().as_tuple().exponent
    places = max(2, -exponent) if isinstance(exponent, int) else 2
    return f"{value:.{places}f}"


def quantity(value: Decimal) -> str:
    return f"{value.normalize():f}"


def equipment(item: Equipment) -> pb2.Equipment:
    spec = item.spec
    message = pb2.Equipment(
        id=str(item.id),
        organizationId=spec.organization_id,
        inventoryNumber=spec.inventory_number,
        name=spec.name,
        purchasePrice=str(spec.purchase_price),
        residualValue=str(spec.residual_value),
        usefulLifeMonths=spec.useful_life_months,
        fuelType=spec.fuel_type.value,
        fuelConsumptionPerHour=str(spec.fuel_consumption_per_hour),
        dailyOverhead=str(spec.daily_overhead),
        currency=spec.currency,
        status=item.status.value,
        version=item.version,
        createdAt=ts(item.created_at),
        updatedAt=ts(item.updated_at),
    )
    if spec.catalog_item_id is not None:
        message.catalogItemId = spec.catalog_item_id
    return message


def rental(item: Rental) -> pb2.Rental:
    message = pb2.Rental(
        id=str(item.id),
        organizationId=item.organization_id,
        equipmentId=str(item.equipment_id),
        workerUserId=item.worker_user_id,
        startsAt=ts(item.starts_at),
        endsAt=ts(item.ends_at),
        status=item.status.value,
        shiftHoursPerDay=str(item.shift_hours_per_day),
        purpose=item.purpose,
        createdByUserId=item.created_by_user_id,
        externalStatus=item.external_status.value,
        createdAt=ts(item.created_at),
        updatedAt=ts(item.updated_at),
    )
    if item.returned_at is not None:
        message.returnedAt.CopyFrom(ts(item.returned_at))
    if item.external_reference is not None:
        message.externalReference = item.external_reference
    return message


def accrual(item: CostAccrual) -> pb2.CostAccrual:
    message = pb2.CostAccrual(
        id=str(item.id),
        accrualDate=item.accrual_date.isoformat(),
        periodStart=ts(item.period_start),
        periodEnd=ts(item.period_end),
        total=amount(item.total),
        currency=item.currency,
        calculatedAt=ts(item.calculated_at),
        lines=[
            pb2.CostLine(
                costType=line.cost_type,
                amount=amount(line.amount),
                quantity=quantity(line.quantity) if line.quantity is not None else None,
                unit=line.unit,
                description=line.description,
            )
            for line in item.lines
        ],
    )
    if item.external_calculation_id is not None:
        message.externalCalculationId = item.external_calculation_id
    return message


def costs(item: RentalCosts) -> pb2.RentalCosts:
    return pb2.RentalCosts(
        rentalId=str(item.rental_id),
        currency=item.currency,
        total=amount(item.total),
        totalsByType=[
            pb2.CostTotal(costType=cost_type, amount=amount(value))
            for cost_type, value in item.totals_by_type().items()
        ],
        accruals=[accrual(entry) for entry in item.accruals],
        complete=item.complete,
    )
