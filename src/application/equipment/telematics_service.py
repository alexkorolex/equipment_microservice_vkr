from __future__ import annotations

import dataclasses
import logging
from datetime import datetime, timedelta
from decimal import Decimal

from src.application.equipment.ports import (
    LinkedEquipment,
    SecretCipher,
    TelematicsAccountRepository,
    TelematicsProvider,
)
from src.domain.equipment.errors import InvalidDataError, TelematicsNotConfiguredError
from src.domain.equipment.models import require_aware
from src.domain.equipment.telematics import (
    TelematicsAccount,
    TelematicsCredentials,
    TelematicsUnit,
    Track,
)

MAX_TRACK_PERIOD = timedelta(days=31)


class TelematicsService:
    """The organization's FortMonitor account: the password is checked with FortMonitor
    before it is stored, stored encrypted and never returned."""

    def __init__(
        self,
        accounts: TelematicsAccountRepository,
        provider: TelematicsProvider,
        cipher: SecretCipher,
        equipment: LinkedEquipment,
        logger: logging.Logger,
    ) -> None:
        self._accounts = accounts
        self._provider = provider
        self._cipher = cipher
        self._equipment = equipment
        self._logger = logger

    async def set_account(
        self, organization_id: int, credentials: TelematicsCredentials, actor_user_id: int
    ) -> TelematicsAccount:
        encrypted = self._cipher.encrypt(credentials.password)
        await self._provider.check(credentials)
        account = await self._accounts.save(
            organization_id, credentials.login.strip(), encrypted, credentials.base_url,
            actor_user_id,
        )
        self._logger.info(
            "Telematics account saved",
            extra={"operation": "set_telematics_account", "organization_id": organization_id,
                   "actor_id": actor_user_id},
        )
        return account

    async def get_account(self, organization_id: int) -> TelematicsAccount | None:
        stored = await self._accounts.get(organization_id)
        return stored.account if stored else None

    async def delete_account(self, organization_id: int) -> None:
        await self._accounts.delete(organization_id)
        self._logger.info(
            "Telematics account removed",
            extra={"operation": "delete_telematics_account", "organization_id": organization_id},
        )

    async def units(self, organization_id: int) -> list[TelematicsUnit]:
        units = await self._provider.units(await self._credentials(organization_id))
        linked = await self._equipment.linked(organization_id)
        return [
            dataclasses.replace(unit, equipment_id=linked.get(unit.object_id)) for unit in units
        ]

    async def unit(self, organization_id: int, object_id: int) -> TelematicsUnit:
        unit = await self._provider.unit(await self._credentials(organization_id), object_id)
        linked = await self._equipment.linked(organization_id)
        return dataclasses.replace(unit, equipment_id=linked.get(unit.object_id))

    async def track(
        self, organization_id: int, object_id: int, start: datetime, end: datetime
    ) -> Track:
        require_aware(start, "startsAt")
        require_aware(end, "endsAt")
        if not start < end <= start + MAX_TRACK_PERIOD:
            raise InvalidDataError("A track covers from 1 second to 31 days")
        return await self._provider.track(
            await self._credentials(organization_id), object_id, start, end
        )

    async def distance_km(
        self, organization_id: int, object_id: int, start: datetime, end: datetime
    ) -> Decimal:
        """Mileage for fuel by 100 km: the accrual worker asks for each day."""
        return (await self.track(organization_id, object_id, start, end)).distance_km

    async def _credentials(self, organization_id: int) -> TelematicsCredentials:
        stored = await self._accounts.get(organization_id)
        if stored is None:
            raise TelematicsNotConfiguredError("FortMonitor is not connected for the organization")
        account = stored.account
        return TelematicsCredentials(
            account.login, self._cipher.decrypt(stored.password_encrypted), account.base_url
        )
