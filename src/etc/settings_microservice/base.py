from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from dotenv import load_dotenv

from src.etc.settings_microservice.sources import (
    ConfigSource,
    ConfigurationError,
    VaultSecretsReader,
)


@dataclass(frozen=True, slots=True)
class CostSystemSettings:
    """HTTP API of the external system that registers rentals and calculates costs."""

    base_url: str
    token: str | None
    timeout_seconds: float


@dataclass(frozen=True, slots=True)
class WorkerSettings:
    submit_interval_seconds: float
    accrual_interval_seconds: float
    batch_size: int
    max_submit_attempts: int


@dataclass(frozen=True, slots=True)
class Settings:
    port: int
    debug: bool
    service_name: str
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str
    otel_host: str
    otel_port: int
    loki_endpoint: str | None
    default_shift_hours: Decimal
    cost_system: CostSystemSettings
    workers: WorkerSettings


class SettingsLoader:
    _VAULT_MOUNT_POINT = "equipment_microservice_kv"

    def load(self) -> Settings:
        load_dotenv()
        source = ConfigSource(self._secrets())
        return Settings(
            port=source.integer("PORT"),
            debug=source.boolean("DEBUG", default=False),
            service_name=source.value("NAME_SERVICE", "equipment-microservice"),
            db_host=source.value("DB_HOST"),
            db_port=source.integer("DB_PORT"),
            db_name=source.value("DB_NAME"),
            db_user=source.value("DB_USER"),
            db_password=source.value("DB_PASSWORD"),
            otel_host=source.value("OTEL_HOST"),
            otel_port=source.integer("OTEL_PORT"),
            loki_endpoint=source.optional("LOKI_ENDPOINT"),
            default_shift_hours=self._shift_hours(source),
            cost_system=self._cost_system(source),
            workers=self._workers(source),
        )

    def _secrets(self) -> dict[str, object]:
        if os.getenv("USE_VAULT", "false").lower() not in {"true", "1", "yes"}:
            return {}
        return dict(VaultSecretsReader(self._VAULT_MOUNT_POINT).read())

    @staticmethod
    def _shift_hours(source: ConfigSource) -> Decimal:
        raw = source.value("DEFAULT_SHIFT_HOURS", "8")
        try:
            hours = Decimal(raw)
        except InvalidOperation as exc:
            raise ConfigurationError("DEFAULT_SHIFT_HOURS must be a number") from exc
        if not Decimal(0) < hours <= Decimal(24):
            raise ConfigurationError("DEFAULT_SHIFT_HOURS must be within (0, 24]")
        return hours

    @staticmethod
    def _cost_system(source: ConfigSource) -> CostSystemSettings:
        settings = CostSystemSettings(
            base_url=source.value("COST_SYSTEM_URL").rstrip("/"),
            token=source.optional("COST_SYSTEM_TOKEN"),
            timeout_seconds=source.number("COST_SYSTEM_TIMEOUT_SECONDS", 10.0),
        )
        if settings.timeout_seconds <= 0:
            raise ConfigurationError("COST_SYSTEM_TIMEOUT_SECONDS must be positive")
        return settings

    @staticmethod
    def _workers(source: ConfigSource) -> WorkerSettings:
        settings = WorkerSettings(
            submit_interval_seconds=source.number("SUBMIT_INTERVAL_SECONDS", 5.0),
            accrual_interval_seconds=source.number("ACCRUAL_INTERVAL_SECONDS", 60.0),
            batch_size=source.integer("WORKER_BATCH_SIZE", 50),
            max_submit_attempts=source.integer("SUBMIT_MAX_ATTEMPTS", 20),
        )
        if min(settings.submit_interval_seconds, settings.accrual_interval_seconds) <= 0:
            raise ConfigurationError("Worker intervals must be positive")
        if settings.batch_size < 1 or settings.max_submit_attempts < 1:
            raise ConfigurationError("WORKER_BATCH_SIZE and SUBMIT_MAX_ATTEMPTS must be positive")
        return settings
