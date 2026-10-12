from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal

DEFAULT_FUEL_PRICES = "diesel=68.50,petrol=59.20,gas=29.90,electric=7.40"


@dataclass(frozen=True, slots=True)
class StubSettings:
    port: int
    fuel_prices: dict[str, Decimal]
    failure_rate: float
    service_name: str
    loki_endpoint: str | None

    @classmethod
    def from_env(cls) -> StubSettings:
        failure_rate = float(os.getenv("STUB_FAILURE_RATE", "0"))
        if not 0 <= failure_rate <= 1:
            raise ValueError("STUB_FAILURE_RATE must be within [0, 1]")
        return cls(
            port=int(os.getenv("PORT", "8090")),
            fuel_prices=parse_prices(os.getenv("STUB_FUEL_PRICES", DEFAULT_FUEL_PRICES)),
            failure_rate=failure_rate,
            service_name=os.getenv("NAME_SERVICE", "cost-system-stub"),
            loki_endpoint=os.getenv("LOKI_ENDPOINT") or None,
        )


def parse_prices(raw: str) -> dict[str, Decimal]:
    """``"diesel=68.50,petrol=59.20"`` into prices per litre (or kWh)."""
    prices: dict[str, Decimal] = {}
    for item in filter(None, (part.strip() for part in raw.split(","))):
        name, _, price = item.partition("=")
        prices[name.strip()] = Decimal(price.strip())
    return prices
