"""FortMonitor through the ``fort-monitor-api`` client: one session per call."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal

from fort_monitor import (
    FortMonitor,
    FortMonitorApiError,
    FortMonitorAuthenticationError,
    FortMonitorError,
    FortMonitorTransportError,
)
from fort_monitor.schemas.objects import Object, ObjectFull
from fort_monitor.schemas.objects import Sensor as FortMonitorSensor

from src.domain.equipment.errors import TelematicsRejectedError, TelematicsUnavailableError
from src.domain.equipment.telematics import (
    MOVING,
    PARKED,
    Sensor,
    TelematicsCredentials,
    TelematicsUnit,
    Track,
    TrackPoint,
)

KM = Decimal("0.1")
# A track point's ``dst`` is in metres: on a real account their sum matched the
# distance between the coordinates within 0.3%.
METRES_PER_KM = Decimal(1000)
# ``move`` is a state code, not a speed: 1xx while moving, 2xx while parked.
MOVE_STATES = {1: MOVING, 2: PARKED}
# Sensors FortMonitor names after the state; their value is its duration ("0:02:43").
STATE_SENSORS = {"Движение": MOVING, "Стоянка": PARKED}
SPEED_SENSOR = "Скорость"
# Full object info is fetched for every vehicle of the list, a few at a time.
UNIT_CONCURRENCY = 5
NUMBER = re.compile(r"-?\d+(?:[.,]\d+)?")


class FortMonitorProvider:
    def __init__(
        self,
        default_base_url: str,
        timeout_seconds: float,
        logger: logging.Logger,
    ) -> None:
        self._default_base_url = default_base_url
        self._timeout = timeout_seconds
        self._logger = logger

    async def check(self, credentials: TelematicsCredentials) -> None:
        async with self._session(credentials) as client:
            if not await client.ping():
                raise TelematicsRejectedError("FortMonitor did not confirm the session")

    async def units(self, credentials: TelematicsCredentials) -> list[TelematicsUnit]:
        """The list lacks sensors, so speed and state come from each object's full info."""
        async with self._session(credentials) as client:
            objects = await client.objects.get_objects_list()
            limit = asyncio.Semaphore(UNIT_CONCURRENCY)

            async def detailed(item: Object) -> TelematicsUnit:
                async with limit:
                    try:
                        return self._full_unit(await client.objects.full_object_info(item.id))
                    except FortMonitorError as exc:
                        self._logger.warning("FortMonitor object %s info failed: %s", item.id, exc)
                        return self._unit(item)

            return list(await asyncio.gather(*(detailed(item) for item in objects)))

    async def unit(self, credentials: TelematicsCredentials, object_id: int) -> TelematicsUnit:
        async with self._session(credentials) as client:
            info = await client.objects.full_object_info(object_id)
        return self._full_unit(info)

    async def track(
        self,
        credentials: TelematicsCredentials,
        object_id: int,
        start: datetime,
        end: datetime,
    ) -> Track:
        async with self._session(credentials) as client:
            data = await client.objects.track(
                oid=object_id, start_time=start.astimezone(UTC), finish_time=end.astimezone(UTC)
            )
        distance = sum((Decimal(str(point.dst)) for point in data.coords), Decimal(0))
        return Track(
            object_id=object_id,
            distance_km=(distance / METRES_PER_KM).quantize(KM),
            points=tuple(
                TrackPoint(utc_time(point.tm), point.lat, point.lon, point.speed)
                for point in data.coords
            ),
        )

    @asynccontextmanager
    async def _session(self, credentials: TelematicsCredentials) -> AsyncIterator[FortMonitor]:
        client = FortMonitor(
            credentials.login,
            credentials.password,
            base_url=credentials.base_url or self._default_base_url,
            timeout=self._timeout,
            logger=self._logger,
        )
        try:
            async with client:
                yield client
        except FortMonitorAuthenticationError as exc:
            raise TelematicsRejectedError("FortMonitor rejected the login or password") from exc
        except FortMonitorTransportError as exc:
            raise TelematicsUnavailableError(f"FortMonitor is unreachable: {exc}") from exc
        except FortMonitorApiError as exc:
            if exc.status is not None and exc.status >= 500:
                raise TelematicsUnavailableError(f"FortMonitor error {exc.status}") from exc
            raise TelematicsRejectedError(f"FortMonitor refused the request: {exc}") from exc
        except (FortMonitorError, KeyError, TypeError, ValueError) as exc:
            raise TelematicsRejectedError(f"Unexpected FortMonitor answer: {exc}") from exc

    @staticmethod
    def _unit(item: Object) -> TelematicsUnit:
        """From the list alone: the state by its code, the speed unknown."""
        return TelematicsUnit(
            object_id=item.id,
            name=item.name,
            imei=item.IMEI,
            status=item.status,
            latitude=item.lat,
            longitude=item.lon,
            direction=item.direction,
            speed=0.0,
            last_data_at=utc_time(item.lastData),
            movement=MOVE_STATES.get(int(item.move) // 100, ""),
        )

    @staticmethod
    def _full_unit(info: ObjectFull) -> TelematicsUnit:
        movement, duration = movement_of(info.move, info.sensors)
        return TelematicsUnit(
            object_id=int(info.oid),
            name=info.name or info.Name,
            imei=info.IMEI,
            status=info.status,
            latitude=info.lat,
            longitude=info.lon,
            direction=info.direction,
            speed=speed_of(info.sensors),
            last_data_at=utc_time(info.dt),
            address=info.address,
            sensors=tuple(
                Sensor(sensor.name, sensor.val, utc_time(sensor.dt)) for sensor in info.sensors
            ),
            movement=movement,
            movement_duration=duration,
        )


def movement_of(move: float, sensors: Sequence[FortMonitorSensor]) -> tuple[str, str]:
    """The state and its duration from the state sensor, else the state by ``move``."""
    for sensor in sensors:
        if sensor.name in STATE_SENSORS:
            return STATE_SENSORS[sensor.name], sensor.val
    return MOVE_STATES.get(int(move) // 100, ""), ""


def speed_of(sensors: Sequence[FortMonitorSensor]) -> float:
    """Km/h from the speed sensor ("5.9 км/ч"); 0 without one."""
    for sensor in sensors:
        if sensor.name == SPEED_SENSOR and (number := NUMBER.search(sensor.val)):
            return float(number.group().replace(",", "."))
    return 0.0


def utc_time(value: str) -> str:
    """FortMonitor writes UTC as "2026-10-28 07:54:14" or "2026-10-28T07:54:14"."""
    return value.replace(" ", "T", 1) if re.match(r"\d{4}-\d{2}-\d{2} \d", value) else value
