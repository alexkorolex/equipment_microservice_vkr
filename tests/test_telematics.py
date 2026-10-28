"""FortMonitor account: encryption at rest, the service over PostgreSQL, adapter errors."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import asyncpg
import pytest
from cryptography.fernet import Fernet
from fort_monitor import (
    FortMonitorApiError,
    FortMonitorAuthenticationError,
    FortMonitorTransportError,
)

from src.application.equipment.telematics_service import TelematicsService
from src.domain.equipment.errors import (
    InvalidDataError,
    TelematicsNotConfiguredError,
    TelematicsRejectedError,
    TelematicsUnavailableError,
)
from src.domain.equipment.telematics import TelematicsCredentials, TelematicsUnit, Track
from src.infrastructure.db.repositories.equipment import PostgresEquipmentRepository
from src.infrastructure.db.repositories.telematics import PostgresTelematicsAccounts
from src.infrastructure.telematics import fortmonitor
from src.infrastructure.telematics.crypto import SecretBox
from tests.common import LOGGER, at, make_spec

KEY = Fernet.generate_key().decode()


class FakeProvider:
    def __init__(self) -> None:
        self.passwords: list[str] = []

    async def check(self, credentials: TelematicsCredentials) -> None:
        self.passwords.append(credentials.password)
        if credentials.password != "secret":
            raise TelematicsRejectedError("FortMonitor rejected the login or password")

    async def units(self, credentials: TelematicsCredentials) -> list[TelematicsUnit]:
        return [TelematicsUnit(501, "КАМАЗ А123ВС", "86000", 1, 55.7, 37.6, 90, 42, "now"),
                TelematicsUnit(502, "Погрузчик", "86001", 0, 55.8, 37.5, 0, 0, "now")]

    async def unit(self, credentials: TelematicsCredentials, object_id: int) -> TelematicsUnit:
        return (await self.units(credentials))[0]

    async def track(self, credentials: TelematicsCredentials, object_id: int,
                    start: datetime, end: datetime) -> Track:
        return Track(object_id, Decimal("87.5"), ())


@pytest.fixture
def service(pool: asyncpg.Pool) -> TelematicsService:
    return TelematicsService(
        PostgresTelematicsAccounts(pool), FakeProvider(), SecretBox(KEY),
        PostgresEquipmentRepository(pool), LOGGER,
    )


async def test_account_is_checked_stored_encrypted_and_hidden(
    service: TelematicsService, pool: asyncpg.Pool
) -> None:
    with pytest.raises(TelematicsNotConfiguredError):
        await service.units(15)
    with pytest.raises(TelematicsRejectedError):
        await service.set_account(15, TelematicsCredentials("api", "wrong"), actor_user_id=3)
    assert await service.get_account(15) is None

    account = await service.set_account(15, TelematicsCredentials(" api ", "secret"), 3)

    assert (account.login, account.updated_by_user_id) == ("api", 3)
    stored = await pool.fetchval(
        "SELECT password_encrypted FROM equipment.telematics_accounts WHERE organization_id = 15"
    )
    assert "secret" not in stored and SecretBox(KEY).decrypt(stored) == "secret"
    with pytest.raises(TelematicsNotConfiguredError):
        SecretBox(Fernet.generate_key().decode()).decrypt(stored)


async def test_units_show_the_linked_equipment_and_give_mileage(
    service: TelematicsService, pool: asyncpg.Pool
) -> None:
    await service.set_account(15, TelematicsCredentials("api", "secret"), 3)
    truck = await PostgresEquipmentRepository(pool).create(
        make_spec(telematics_object_id=501), created_by_user_id=3
    )

    units = await service.units(15)

    assert [(unit.object_id, unit.equipment_id) for unit in units] == [(501, truck.id), (502, None)]
    assert await service.distance_km(15, 501, at(1), at(2)) == Decimal("87.5")
    with pytest.raises(InvalidDataError):
        await service.track(15, 501, at(1), at(3, 1).replace(month=12))
    await service.delete_account(15)
    assert await service.get_account(15) is None


def test_disabled_without_a_key() -> None:
    with pytest.raises(TelematicsNotConfiguredError):
        SecretBox(None).encrypt("secret")


class FakeFortMonitor:
    """Stands in for ``fort_monitor.FortMonitor``; ``error`` is raised on connect."""

    error: Exception | None = None

    def __init__(self, login: str, password: str, **options: Any) -> None:
        self.objects = SimpleNamespace(
            track=self._track, get_objects_list=self._objects, full_object_info=self._full
        )

    async def __aenter__(self) -> "FakeFortMonitor":
        if self.error is not None:
            raise self.error
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    @staticmethod
    async def _objects() -> list[Any]:
        listed = dict(IMEI="86", status=5, lat=55.7, lon=37.6, direction=0.0)
        return [SimpleNamespace(id=1461, name="ACE", move=111.0, lastData="2026-10-28T07:54:14",
                                **listed),
                SimpleNamespace(id=2365, name="SITRAK", move=211.0,
                                lastData="2026-10-28T07:54:05", **listed)]

    @staticmethod
    async def _full(oid: int) -> Any:
        if oid == 2365:
            raise FortMonitorApiError("object is blocked", status=403)
        sensors = [SimpleNamespace(name="Движение", val="0:02:43", dt="2026-10-28 07:54:14"),
                   SimpleNamespace(name="Скорость", val="5.9 км/ч", dt="2026-10-28 07:54:14")]
        return SimpleNamespace(oid="1461", name="ACE", Name="ACE", IMEI="86", status=5, lat=55.7,
                               lon=37.6, direction=0.0, move=111.0, dt="2026-10-28 07:54:14",
                               address="Оренбург", sensors=sensors)

    @staticmethod
    async def _track(oid: int, start_time: datetime, finish_time: datetime) -> Any:
        point = SimpleNamespace(tm="t", lat=1.0, lon=2.0, speed=30.0)
        return SimpleNamespace(coords=[SimpleNamespace(dst=1200.0, **vars(point)),
                                       SimpleNamespace(dst=800.0, **vars(point))])


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (FortMonitorAuthenticationError("bad login", status=401), TelematicsRejectedError),
        (FortMonitorTransportError("timeout"), TelematicsUnavailableError),
    ],
)
async def test_adapter_maps_library_errors(
    monkeypatch: pytest.MonkeyPatch, error: Exception, expected: type[Exception]
) -> None:
    monkeypatch.setattr(fortmonitor, "FortMonitor", FakeFortMonitor)
    monkeypatch.setattr(FakeFortMonitor, "error", error)
    provider = fortmonitor.FortMonitorProvider("https://fm.test/api/", 5, LOGGER)

    with pytest.raises(expected):
        await provider.track(TelematicsCredentials("api", "secret"), 501, at(1), at(2))


async def test_adapter_sums_track_distance_in_metres(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fortmonitor, "FortMonitor", FakeFortMonitor)
    provider = fortmonitor.FortMonitorProvider("https://fm.test/api/", 5, LOGGER)

    track = await provider.track(TelematicsCredentials("api", "secret"), 501, at(1), at(2))

    assert (track.distance_km, len(track.points)) == (Decimal("2.0"), 2)


async def test_adapter_reads_speed_and_state_from_sensors_not_move(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fortmonitor, "FortMonitor", FakeFortMonitor)
    monkeypatch.setattr(FakeFortMonitor, "error", None)
    provider = fortmonitor.FortMonitorProvider("https://fm.test/api/", 5, LOGGER)

    moving, parked = await provider.units(TelematicsCredentials("api", "secret"))

    # "move" is a state code (111, 211), never shown as km/h.
    assert (moving.movement, moving.movement_duration, moving.speed) == ("moving", "0:02:43", 5.9)
    assert moving.last_data_at == "2026-10-28T07:54:14"
    # Its full info failed: the state comes from the code, the speed stays unknown.
    assert (parked.movement, parked.speed) == ("parked", 0.0)

