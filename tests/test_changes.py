"""Change events for live updates: written by triggers, published to Kafka."""

from __future__ import annotations

from typing import Any

import asyncpg

from src.infrastructure.changes.publisher import TOPIC, ChangePublisher
from src.infrastructure.db.repositories.equipment import PostgresEquipmentRepository
from src.infrastructure.db.repositories.rentals import PostgresRentalRepository
from tests.common import at, make_request, make_spec


class RecordingBroker:
    def __init__(self) -> None:
        self.messages: list[tuple[dict[str, Any], str, dict[str, Any]]] = []

    async def publish(self, message: dict[str, Any], topic: str, **kwargs: Any) -> None:
        self.messages.append((message, topic, kwargs))


async def _events(pool: asyncpg.Pool) -> list[tuple[str, str, int | None]]:
    rows = await pool.fetch(
        "SELECT event, entity_id, version FROM equipment.change_events ORDER BY occurred_at"
    )
    return [(row["event"], row["entity_id"], row["version"]) for row in rows]


async def test_every_write_of_equipment_records_one_event(pool: asyncpg.Pool) -> None:
    repository = PostgresEquipmentRepository(pool)
    created = await repository.create(make_spec(), created_by_user_id=3)
    await pool.execute(
        "UPDATE equipment.equipment SET name = 'Новое', version = version + 1 WHERE id = $1",
        created.id,
    )
    # Nothing changed: no event.
    await pool.execute("UPDATE equipment.equipment SET name = name WHERE id = $1", created.id)

    assert await _events(pool) == [
        ("equipment.created", str(created.id), 1),
        ("equipment.updated", str(created.id), 2),
    ]


async def test_publisher_sends_events_keyed_by_organization_and_marks_them(
    pool: asyncpg.Pool,
) -> None:
    created = await PostgresEquipmentRepository(pool).create(make_spec(), created_by_user_id=3)
    broker = RecordingBroker()
    publisher = ChangePublisher(pool, broker, "equipment.change_events")  # type: ignore[arg-type]

    assert await publisher.run_once() == 1
    assert await publisher.run_once() == 0

    [(message, topic, kwargs)] = broker.messages
    assert topic == TOPIC
    assert kwargs["key"] == str(created.spec.organization_id).encode()
    assert {k: message[k] for k in ("event", "entity", "entity_id", "organization_id", "changes")} == {
        "event": "equipment.created",
        "entity": "equipment",
        "entity_id": str(created.id),
        "organization_id": created.spec.organization_id,
        "changes": None,
    }
    unpublished = await pool.fetchval(
        "SELECT count(*) FROM equipment.change_events WHERE published_at IS NULL"
    )
    assert unpublished == 0


async def test_failed_publish_leaves_events_for_the_next_run(pool: asyncpg.Pool) -> None:
    await PostgresEquipmentRepository(pool).create(make_spec(), created_by_user_id=3)

    class Down(RecordingBroker):
        async def publish(self, message: dict[str, Any], topic: str, **kwargs: Any) -> None:
            raise ConnectionError("kafka unreachable")

    try:
        await ChangePublisher(pool, Down(), "equipment.change_events").run_once()  # type: ignore[arg-type]
    except ConnectionError:
        pass
    broker = RecordingBroker()

    assert await ChangePublisher(pool, broker, "equipment.change_events").run_once() == 1  # type: ignore[arg-type]


async def test_rental_events_name_the_worker_who_may_see_them(pool: asyncpg.Pool) -> None:
    equipment = await PostgresEquipmentRepository(pool).create(make_spec(), created_by_user_id=3)
    rental = await PostgresRentalRepository(pool).open(make_request(equipment.id), now=at(1))
    await pool.execute(
        """
        INSERT INTO equipment.cost_accruals
            (id, rental_id, accrual_date, period_start, period_end, currency)
        VALUES (gen_random_uuid(), $1, $2, $3, $4, 'RUB')
        """,
        rental.id, at(1).date(), at(1, 8), at(2),
    )

    rows = await pool.fetch(
        "SELECT event, user_id FROM equipment.change_events "
        "WHERE entity = 'rental' AND entity_id = $1 ORDER BY occurred_at",
        str(rental.id),
    )
    # Opening a rental changes the equipment too; an accrual is a change of the rental.
    assert [(row["event"], row["user_id"]) for row in rows] == [
        ("rental.created", 7),
        ("rental.updated", 7),
    ]
