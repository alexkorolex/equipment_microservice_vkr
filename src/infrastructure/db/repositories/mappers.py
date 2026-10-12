from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.domain.equipment.models import (
    CostAccrual,
    CostLine,
    Equipment,
    EquipmentSpec,
    EquipmentStatus,
    ExternalStatus,
    FuelType,
    Rental,
    RentalStatus,
)

Row = Mapping[str, Any]

EQUIPMENT_COLUMNS = """
    e.id, e.organization_id, e.inventory_number, e.name, e.catalog_item_id,
    e.purchase_price, e.residual_value, e.useful_life_months, e.fuel_type,
    e.fuel_consumption_per_hour, e.daily_overhead, e.currency, e.status, e.version,
    e.created_at, e.updated_at
"""

RENTAL_COLUMNS = """
    r.id, r.organization_id, r.equipment_id, r.worker_user_id, r.starts_at, r.ends_at,
    r.returned_at, r.status, r.shift_hours_per_day, r.purpose, r.created_by_user_id,
    r.external_status, r.external_reference, r.created_at, r.updated_at
"""


def spec_from_row(row: Row) -> EquipmentSpec:
    return EquipmentSpec(
        organization_id=int(row["organization_id"]),
        inventory_number=row["inventory_number"],
        name=row["name"],
        catalog_item_id=row["catalog_item_id"],
        purchase_price=row["purchase_price"],
        residual_value=row["residual_value"],
        useful_life_months=int(row["useful_life_months"]),
        fuel_type=FuelType(row["fuel_type"]),
        fuel_consumption_per_hour=row["fuel_consumption_per_hour"],
        daily_overhead=row["daily_overhead"],
        currency=str(row["currency"]).strip(),
    )


def equipment_from_row(row: Row) -> Equipment:
    return Equipment(
        id=row["id"],
        spec=spec_from_row(row),
        status=EquipmentStatus(row["status"]),
        version=int(row["version"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def rental_from_row(row: Row) -> Rental:
    return Rental(
        id=row["id"],
        organization_id=int(row["organization_id"]),
        equipment_id=row["equipment_id"],
        worker_user_id=int(row["worker_user_id"]),
        starts_at=row["starts_at"],
        ends_at=row["ends_at"],
        returned_at=row["returned_at"],
        status=RentalStatus(row["status"]),
        shift_hours_per_day=row["shift_hours_per_day"],
        purpose=row["purpose"],
        created_by_user_id=int(row["created_by_user_id"]),
        external_status=ExternalStatus(row["external_status"]),
        external_reference=row["external_reference"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def accrual_from_rows(row: Row, lines: Sequence[Row]) -> CostAccrual:
    return CostAccrual(
        id=row["id"],
        rental_id=row["rental_id"],
        accrual_date=row["accrual_date"],
        period_start=row["period_start"],
        period_end=row["period_end"],
        currency=str(row["currency"]).strip(),
        external_calculation_id=row["external_calculation_id"],
        calculated_at=row["calculated_at"],
        lines=tuple(
            CostLine(
                cost_type=line["cost_type"],
                amount=line["amount"],
                quantity=line["quantity"],
                unit=line["unit"],
                description=line["description"],
            )
            for line in lines
        ),
    )
