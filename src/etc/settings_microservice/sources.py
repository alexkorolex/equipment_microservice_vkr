from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

import hvac


class ConfigurationError(RuntimeError):
    """Raised when a required runtime setting is unavailable or malformed."""


class VaultSecretsReader:
    def __init__(self, mount_point: str) -> None:
        self._mount_point = mount_point

    def read(self) -> Mapping[str, Any]:
        client = hvac.Client(
            url=self._environment("VAULT_URL"),
            token=self._environment("VAULT_TOKEN"),
        )
        try:
            response = client.secrets.kv.v2.read_secret_version(
                path="data",
                mount_point=self._mount_point,
            )
            data = response["data"]["data"]
        except Exception as exc:
            raise ConfigurationError("Unable to load Vault secrets") from exc
        if not isinstance(data, Mapping):
            raise ConfigurationError("Vault secret payload must be an object")
        return dict(data)

    @staticmethod
    def _environment(key: str) -> str:
        value = os.getenv(key)
        if value is None:
            raise ConfigurationError(f"Environment variable '{key}' is required")
        return value


class ConfigSource:
    """Reads typed values from Vault secrets first, then from environment."""

    _TRUE_VALUES = frozenset({"true", "1", "yes"})
    _FALSE_VALUES = frozenset({"false", "0", "no"})

    def __init__(self, secrets: Mapping[str, Any]) -> None:
        self._secrets = secrets

    def value(self, key: str, default: str | None = None) -> str:
        value = self.optional(key) or default
        if value is None:
            raise ConfigurationError(f"Configuration value '{key}' is missing")
        return value

    def optional(self, key: str) -> str | None:
        value = self._secrets.get(key)
        if value is None:
            value = os.getenv(key)
        return str(value) if value not in {None, ""} else None

    def integer(self, key: str, default: int | None = None) -> int:
        raw = self.value(key, None if default is None else str(default))
        try:
            return int(raw)
        except ValueError as exc:
            raise ConfigurationError(f"{key} must be an integer") from exc

    def number(self, key: str, default: float) -> float:
        raw = self.value(key, str(default))
        try:
            return float(raw)
        except ValueError as exc:
            raise ConfigurationError(f"{key} must be a number") from exc

    def boolean(self, key: str, default: bool) -> bool:
        value = self.optional_boolean(key)
        return default if value is None else value

    def optional_boolean(self, key: str) -> bool | None:
        value = self.optional(key)
        if value is None:
            return None
        normalized = value.lower()
        if normalized in self._TRUE_VALUES:
            return True
        if normalized in self._FALSE_VALUES:
            return False
        raise ConfigurationError(f"{key} must be a boolean")
