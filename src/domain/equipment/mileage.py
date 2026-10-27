"""Km driven in an accrual period, for equipment whose fuel is counted per 100 km.

Without a tracker the rental has odometer readings taken on issue and on return;
the km between them are spread over the rental in proportion to time, so each day
gets its share once the equipment is back.
"""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from src.domain.equipment.periods import AccrualPeriod

KM = Decimal("0.1")


def odometer_share(
    period: AccrualPeriod,
    starts_at: datetime,
    returned_at: datetime,
    odometer_start: Decimal,
    odometer_end: Decimal,
) -> Decimal:
    total = (returned_at - starts_at).total_seconds()
    if total <= 0:
        return Decimal(0)
    part = Decimal(str((period.end - period.start).total_seconds())) / Decimal(str(total))
    return ((odometer_end - odometer_start) * part).quantize(KM, ROUND_HALF_UP)
