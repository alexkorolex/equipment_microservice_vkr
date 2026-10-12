from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from src.domain.equipment.models import EquipmentSpec, FuelType, RentalRequest

LOGGER = logging.getLogger("equipment-tests")


def ensure_test_env() -> None:
    defaults = {
        "USE_VAULT": "false",
        "PORT": "50055",
        "DB_HOST": "127.0.0.1",
        "DB_PORT": "55432",
        "DB_NAME": "equipment_test",
        "DB_USER": "test",
        "DB_PASSWORD": "test",
        "OTEL_HOST": "localhost",
        "OTEL_PORT": "4318",
        "NAME_SERVICE": "equipment-tests",
        "COST_SYSTEM_URL": "http://cost-system.test",
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, value)


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


def make_spec(**overrides: Any) -> EquipmentSpec:
    values: dict[str, Any] = {
        "organization_id": 15,
        "inventory_number": "TR-001",
        "name": "Трактор МТЗ-82",
        "catalog_item_id": None,
        "purchase_price": Decimal("3650000.00"),
        "residual_value": Decimal("350000.00"),
        "useful_life_months": 120,
        "fuel_type": FuelType.DIESEL,
        "fuel_consumption_per_hour": Decimal("9.500"),
        "daily_overhead": Decimal("1200.00"),
        "currency": "RUB",
    }
    values.update(overrides)
    return EquipmentSpec(**values)


def make_request(equipment_id: UUID, **overrides: Any) -> RentalRequest:
    values: dict[str, Any] = {
        "organization_id": 15,
        "equipment_id": equipment_id,
        "worker_user_id": 7,
        "starts_at": at(1, 8),
        "ends_at": at(5, 18),
        "shift_hours_per_day": Decimal("8"),
        "purpose": "Вспашка поля",
        "created_by_user_id": 3,
    }
    values.update(overrides)
    return RentalRequest(**values)


class FixedClock:
    def __init__(self, now: datetime) -> None:
        self.current = now

    def now(self) -> datetime:
        return self.current
