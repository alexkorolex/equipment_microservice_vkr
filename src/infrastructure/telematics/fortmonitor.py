"""FortMonitor through the ``fort-monitor-api`` client: one session per call."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
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

from src.domain.equipment.errors import TelematicsRejectedError, TelematicsUnavailableError
from src.domain.equipment.telematics import (
    Sensor,
    TelematicsCredentials,
    TelematicsUnit,
    Track,
    TrackPoint,
)

KM = Decimal("0.1")
# Units FortMonitor may report a track point's distance in; see FORTMONITOR_TRACK_DISTANCE_UNIT.
DISTANCE_UNITS = {"km": Decimal(1), "m": Decimal("0.001")}


class FortMonitorProvider:
    def __init__(
        self,
        default_base_url: str,
        timeout_seconds: float,
        logger: logging.Logger,
        distance_unit: str = "km",
    ) -> None:
        if distance_unit not in DISTANCE_UNITS:
            raise ValueError(f"Unknown track distance unit: {distance_unit}")
        self._default_base_url = default_base_url
        self._timeout = timeout_seconds
        self._logger = logger
        self._distance_factor = DISTANCE_UNITS[distance_unit]

    async def check(self, credentials: TelematicsCredentials) -> None:
        async with self._session(credentials) as client:
            if not await client.ping():
                raise TelematicsRejectedError("FortMonitor did not confirm the session")

    async def units(self, credentials: TelematicsCredentials) -> list[TelematicsUnit]:
        async with self._session(credentials) as client:
            objects = await client.objects.get_objects_list()
        return [self._unit(item) for item in objects]

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
            distance_km=(distance * self._distance_factor).quantize(KM),
            points=tuple(
                TrackPoint(point.tm, point.lat, point.lon, point.speed) for point in data.coords
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
        return TelematicsUnit(
            object_id=item.id,
            name=item.name,
            imei=item.IMEI,
            status=item.status,
            latitude=item.lat,
            longitude=item.lon,
            direction=item.direction,
            speed=item.move,
            last_data_at=item.lastData,
        )

    @staticmethod
    def _full_unit(info: ObjectFull) -> TelematicsUnit:
        return TelematicsUnit(
            object_id=int(info.oid),
            name=info.name or info.Name,
            imei=info.IMEI,
            status=info.status,
            latitude=info.lat,
            longitude=info.lon,
            direction=info.direction,
            speed=info.move,
            last_data_at=info.dt,
            address=info.address,
            sensors=tuple(Sensor(sensor.name, sensor.val, sensor.dt) for sensor in info.sensors),
        )
