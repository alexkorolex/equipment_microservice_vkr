from __future__ import annotations

import asyncpg

from src.domain.equipment.telematics import StoredTelematicsAccount, TelematicsAccount
from src.infrastructure.db.repositories.mappers import Row

_COLUMNS = """organization_id, provider, login, password_encrypted, base_url,
    updated_by_user_id, updated_at"""


class PostgresTelematicsAccounts:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get(self, organization_id: int) -> StoredTelematicsAccount | None:
        row = await self._pool.fetchrow(
            f"SELECT {_COLUMNS} FROM equipment.telematics_accounts WHERE organization_id = $1",
            organization_id,
        )
        return self._stored(row) if row is not None else None

    async def save(
        self,
        organization_id: int,
        login: str,
        password_encrypted: str,
        base_url: str,
        actor_user_id: int,
    ) -> TelematicsAccount:
        row = await self._pool.fetchrow(
            f"""
            INSERT INTO equipment.telematics_accounts (
                organization_id, login, password_encrypted, base_url, updated_by_user_id
            ) VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (organization_id) DO UPDATE SET
                login = EXCLUDED.login, password_encrypted = EXCLUDED.password_encrypted,
                base_url = EXCLUDED.base_url, updated_by_user_id = EXCLUDED.updated_by_user_id,
                updated_at = now()
            RETURNING {_COLUMNS}""",
            organization_id,
            login,
            password_encrypted,
            base_url,
            actor_user_id,
        )
        return self._stored(row).account

    async def delete(self, organization_id: int) -> None:
        await self._pool.execute(
            "DELETE FROM equipment.telematics_accounts WHERE organization_id = $1",
            organization_id,
        )

    @staticmethod
    def _stored(row: Row) -> StoredTelematicsAccount:
        return StoredTelematicsAccount(
            account=TelematicsAccount(
                organization_id=int(row["organization_id"]),
                login=row["login"],
                base_url=row["base_url"],
                updated_by_user_id=int(row["updated_by_user_id"]),
                updated_at=row["updated_at"],
                provider=row["provider"],
            ),
            password_encrypted=row["password_encrypted"],
        )
