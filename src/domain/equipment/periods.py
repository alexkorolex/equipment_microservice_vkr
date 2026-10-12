"""Which days of a rental are due for a cost accrual.

Costs accrue per UTC calendar day. A day is accrued once it is over: for a rental
still open, up to the last midnight; for a returned one, up to the return time,
so the first and the last day may be partial. An open rental past its planned end
keeps accruing - the equipment is still with the worker.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta


@dataclass(frozen=True, slots=True)
class AccrualPeriod:
    day: date
    start: datetime
    end: datetime

    @property
    def hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600


def day_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


def accrual_horizon(returned_at: datetime | None, now: datetime) -> datetime:
    """Accruals may cover time up to this moment."""
    if returned_at is not None:
        return returned_at.astimezone(UTC)
    return day_start(now.astimezone(UTC).date())


def due_periods(
    starts_at: datetime,
    returned_at: datetime | None,
    now: datetime,
    last_accrued_day: date | None,
    limit: int,
) -> list[AccrualPeriod]:
    """The next not yet accrued days, oldest first, at most ``limit`` of them."""
    start = starts_at.astimezone(UTC)
    horizon = accrual_horizon(returned_at, now)
    day = last_accrued_day + timedelta(days=1) if last_accrued_day else start.date()
    periods: list[AccrualPeriod] = []
    while len(periods) < limit and day_start(day) < horizon:
        period_start = max(day_start(day), start)
        period_end = min(day_start(day + timedelta(days=1)), horizon)
        if period_end > period_start:
            periods.append(AccrualPeriod(day, period_start, period_end))
        day += timedelta(days=1)
    return periods


def accruals_complete(
    starts_at: datetime,
    returned_at: datetime | None,
    last_accrued_day: date | None,
) -> bool:
    """True once a returned rental has every day up to the return accrued."""
    if returned_at is None:
        return False
    far_future = returned_at + timedelta(days=1)
    return not due_periods(starts_at, returned_at, far_future, last_accrued_day, limit=1)
