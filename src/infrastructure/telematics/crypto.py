"""Encryption of telematics passwords at rest (Fernet: AES-128-CBC with HMAC-SHA256)."""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from src.domain.equipment.errors import TelematicsNotConfiguredError


class SecretBox:
    """Encrypts with the service key; without a key telematics cannot be used."""

    def __init__(self, key: str | None) -> None:
        self._fernet = Fernet(key.encode()) if key else None

    @property
    def available(self) -> bool:
        return self._fernet is not None

    def encrypt(self, secret: str) -> str:
        return self._required().encrypt(secret.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._required().decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise TelematicsNotConfiguredError(
                "The stored telematics password cannot be decrypted: enter it again"
            ) from exc

    def _required(self) -> Fernet:
        if self._fernet is None:
            raise TelematicsNotConfiguredError("Telematics is not enabled on this server")
        return self._fernet
