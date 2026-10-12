from datetime import UTC, date, datetime, timedelta, timezone

from src.domain.equipment.periods import accruals_complete, due_periods


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


def spans(periods: list) -> list[tuple[date, float]]:
    return [(p.day, p.hours) for p in periods]


def test_open_rental_accrues_only_finished_days() -> None:
    periods = due_periods(at(1, 18), None, now=at(3, 10), last_accrued_day=None, limit=10)

    assert spans(periods) == [(date(2026, 10, 1), 6.0), (date(2026, 10, 2), 24.0)]


def test_returned_rental_accrues_partial_last_day_and_is_complete() -> None:
    periods = due_periods(at(1, 18), at(3, 9, 30), now=at(3, 10), last_accrued_day=None, limit=10)

    assert spans(periods) == [
        (date(2026, 10, 1), 6.0),
        (date(2026, 10, 2), 24.0),
        (date(2026, 10, 3), 9.5),
    ]
    assert accruals_complete(at(1, 18), at(3, 9, 30), date(2026, 10, 3))
    assert not accruals_complete(at(1, 18), at(3, 9, 30), date(2026, 10, 2))


def test_return_at_midnight_adds_no_empty_day() -> None:
    periods = due_periods(at(1, 12), at(3), now=at(5), last_accrued_day=None, limit=10)

    assert [p.day for p in periods] == [date(2026, 10, 1), date(2026, 10, 2)]
    assert accruals_complete(at(1, 12), at(3), date(2026, 10, 2))


def test_continues_after_last_accrued_day_with_limit() -> None:
    periods = due_periods(at(1), None, now=at(10, 5), last_accrued_day=date(2026, 10, 4), limit=2)

    assert [p.day for p in periods] == [date(2026, 10, 5), date(2026, 10, 6)]


def test_future_or_same_day_rental_has_nothing_due() -> None:
    assert due_periods(at(5), None, now=at(3), last_accrued_day=None, limit=5) == []
    assert due_periods(at(3, 8), None, now=at(3, 20), last_accrued_day=None, limit=5) == []


def test_overdue_open_rental_keeps_accruing() -> None:
    # Planned end is irrelevant here: the equipment has not come back.
    periods = due_periods(at(1), None, now=at(4, 1), last_accrued_day=None, limit=10)

    assert len(periods) == 3


def test_other_time_zones_are_normalised_to_utc() -> None:
    moscow = timezone(timedelta(hours=3))
    starts = datetime(2026, 10, 2, 1, 0, tzinfo=moscow)  # 2026-10-01 22:00 UTC

    periods = due_periods(starts, None, now=at(3, 5), last_accrued_day=None, limit=10)

    assert spans(periods) == [(date(2026, 10, 1), 2.0), (date(2026, 10, 2), 24.0)]


def test_open_rental_is_never_complete() -> None:
    assert not accruals_complete(at(1), None, date(2026, 10, 20))
