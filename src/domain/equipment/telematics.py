"""The organization's telematics account (FortMonitor) and what it reports."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from src.domain.equipment.errors import InvalidDataError


@dataclass(frozen=True, slots=True)
class TelematicsCredentials:
    login: str
    password: str
    # Empty: the provider's default server.
    base_url: str = ""

    def __post_init__(self) -> None:
        if not self.login.strip() or not self.password:
            raise InvalidDataError("login and password are required")
        if len(self.login) > 200 or len(self.password) > 500:
            raise InvalidDataError("login or password is too long")
        if self.base_url and not self.base_url.startswith("https://"):
            raise InvalidDataError("baseUrl must be an https:// address")
        if len(self.base_url) > 500:
            raise InvalidDataError("baseUrl is too long")


@dataclass(frozen=True, slots=True)
class TelematicsAccount:
    """What the CRM may see of the account: never the password."""

    organization_id: int
    login: str
    base_url: str
    updated_by_user_id: int
    updated_at: datetime
    provider: str = "fortmonitor"


@dataclass(frozen=True, slots=True)
class StoredTelematicsAccount:
    account: TelematicsAccount
    password_encrypted: str


@dataclass(frozen=True, slots=True)
class Sensor:
    name: str
    value: str
    measured_at: str


@dataclass(frozen=True, slots=True)
class TelematicsUnit:
    object_id: int
    name: str
    imei: str
    status: int
    latitude: float
    longitude: float
    direction: float
    speed: float
    last_data_at: str
    address: str = ""
    sensors: tuple[Sensor, ...] = field(default_factory=tuple)
    equipment_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class TrackPoint:
    at: str
    latitude: float
    longitude: float
    speed: float


@dataclass(frozen=True, slots=True)
class Track:
    object_id: int
    distance_km: Decimal
    points: tuple[TrackPoint, ...]
