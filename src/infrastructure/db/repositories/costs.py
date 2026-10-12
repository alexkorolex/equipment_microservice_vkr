from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import UUID, uuid4

import asyncpg

from src.application.equipment.commands import AccrualCandidate, CostCalculation
from src.domain.equipment.errors import NotFoundError
from src.domain.equipment.models import RentalCosts
from src.domain.equipment.periods import AccrualPeriod
from src.infrastructure.db.repositories.mappers import (
    EQUIPMENT_COLUMNS,
    Row,
    accrual_from_rows,
    spec_from_row,
)

# Rentals that may have days to accrue; the ones furthest behind come first.
_CANDIDATES = f"""
    SELECT {EQUIPMENT_COLUMNS},
        r.id AS rental_id, r.starts_at, r.returned_at, r.shift_hours_per_day,
        r.external_reference,
        (SELECT max(a.accrual_date) FROM equipment.cost_accruals a
         WHERE a.rental_id = r.id) AS last_accrued_day
    FROM equipment.rentals r
    JOIN equipment.equipment e ON e.id = r.equipment_id
    WHERE r.status IN ('open', 'returned') AND NOT r.costs_complete
      AND r.external_status = 'submitted' AND r.starts_at < $1
    ORDER BY last_accrued_day NULLS FIRST, r.starts_at
    LIMIT $2
"""


class PostgresCostRepository:
    def __init__(self, pool: asyncpg.Pool, id_factory: Callable[[], UUID] = uuid4) -> None:
        self._pool = pool
        self._id_factory = id_factory

    async def costs(self, organization_id: int, rental_id: UUID) -> RentalCosts:
        async with self._pool.acquire() as connection:
            rental = await connection.fetchrow(
                """SELECT r.costs_complete, e.currency FROM equipment.rentals r
                JOIN equipment.equipment e ON e.id = r.equipment_id
                WHERE r.id = $1 AND r.organization_id = $2""",
                rental_id,
                organization_id,
            )
            if rental is None:
                raise NotFoundError("Rental not found")
            accruals = await connection.fetch(
                """SELECT * FROM equipment.cost_accruals
                WHERE rental_id = $1 ORDER BY accrual_date""",
                rental_id,
            )
            lines = await self._lines(connection, [row["id"] for row in accruals])
        return RentalCosts(
            rental_id=rental_id,
            currency=str(rental["currency"]).strip(),
            accruals=tuple(accrual_from_rows(row, lines.get(row["id"], [])) for row in accruals),
            complete=bool(rental["costs_complete"]),
        )

    async def accrual_candidates(self, now: datetime, limit: int) -> list[AccrualCandidate]:
        rows = await self._pool.fetch(_CANDIDATES, now, limit)
        return [
            AccrualCandidate(
                rental_id=row["rental_id"],
                organization_id=int(row["organization_id"]),
                starts_at=row["starts_at"],
                returned_at=row["returned_at"],
                shift_hours_per_day=row["shift_hours_per_day"],
                external_reference=row["external_reference"],
                last_accrued_day=row["last_accrued_day"],
                spec=spec_from_row(row),
            )
            for row in rows
        ]

    async def save_accrual(
        self, rental_id: UUID, period: AccrualPeriod, calculation: CostCalculation
    ) -> bool:
        """Store one day with its lines; ``False`` when the day is already stored."""
        async with self._pool.acquire() as connection, connection.transaction():
            accrual_id = await connection.fetchval(
                """INSERT INTO equipment.cost_accruals (
                    id, rental_id, accrual_date, period_start, period_end, currency,
                    external_calculation_id
                ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (rental_id, accrual_date) DO NOTHING
                RETURNING id""",
                self._id_factory(),
                rental_id,
                period.day,
                period.start,
                period.end,
                calculation.currency,
                calculation.calculation_id,
            )
            if accrual_id is None:
                return False
            await self._insert_lines(connection, accrual_id, calculation)
        return True

    @staticmethod
    async def _insert_lines(
        connection: asyncpg.Connection, accrual_id: UUID, calculation: CostCalculation
    ) -> None:
        await connection.executemany(
            """INSERT INTO equipment.cost_lines (
                accrual_id, position, cost_type, amount, quantity, unit, description
            ) VALUES ($1, $2, $3, $4, $5, $6, $7)""",
            [
                (accrual_id, position, line.cost_type, line.amount,
                 line.quantity, line.unit, line.description)
                for position, line in enumerate(calculation.lines)
            ],
        )

    async def mark_complete(self, rental_id: UUID) -> None:
        await self._pool.execute(
            """UPDATE equipment.rentals SET costs_complete = TRUE, updated_at = now()
            WHERE id = $1""",
            rental_id,
        )

    @staticmethod
    async def _lines(
        connection: asyncpg.Connection, accrual_ids: list[UUID]
    ) -> dict[UUID, list[Row]]:
        rows = await connection.fetch(
            """SELECT * FROM equipment.cost_lines
            WHERE accrual_id = ANY($1::uuid[]) ORDER BY accrual_id, position""",
            accrual_ids,
        )
        grouped: dict[UUID, list[Row]] = {}
        for row in rows:
            grouped.setdefault(row["accrual_id"], []).append(row)
        return grouped
