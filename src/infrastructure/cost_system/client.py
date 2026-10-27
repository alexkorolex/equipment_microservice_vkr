"""HTTP client of the external cost system (API contract: README, "Внешняя система")."""

from __future__ import annotations

import json
import logging
from decimal import Decimal, InvalidOperation
from typing import Any

import aiohttp

from src.application.equipment.commands import (
    CalculatedLine,
    CostCalculation,
    CostCalculationRequest,
)
from src.application.equipment.errors import (
    CostSystemRejectedError,
    CostSystemUnavailableError,
)
from src.etc.settings_microservice.base import CostSystemSettings


class HttpCostSystem:
    _RETRYABLE_STATUSES = frozenset({408, 425, 429})

    def __init__(
        self,
        settings: CostSystemSettings,
        session: aiohttp.ClientSession,
        logger: logging.Logger,
    ) -> None:
        self._settings = settings
        self._session = session
        self._logger = logger

    async def register_rental(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/v1/rentals", payload)

    async def notify_return(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post(f"/v1/rentals/{payload['rentalId']}/return", payload)

    async def notify_cancel(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post(f"/v1/rentals/{payload['rentalId']}/cancel", payload)

    async def calculate(self, request: CostCalculationRequest) -> CostCalculation:
        key = f"{request.rental_id}:{request.period.day.isoformat()}"
        answer = await self._post("/v1/cost-calculations", self._calculation_body(request), key)
        return self._calculation(answer)

    async def _post(
        self, path: str, body: dict[str, Any], idempotency_key: str | None = None
    ) -> dict[str, Any]:
        try:
            async with self._session.post(
                self._settings.base_url + path,
                json=body,
                headers=self._headers(idempotency_key),
                timeout=aiohttp.ClientTimeout(total=self._settings.timeout_seconds),
            ) as response:
                status, text = response.status, await response.text()
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise CostSystemUnavailableError(f"{path}: {type(exc).__name__}") from exc
        self._logger.info(
            "Cost system call", extra={"operation": "cost_system", "path": path, "status": status}
        )
        if status >= 500 or status in self._RETRYABLE_STATUSES:
            raise CostSystemUnavailableError(f"{path}: HTTP {status}")
        if status >= 400:
            raise CostSystemRejectedError(f"{path}: HTTP {status}: {text[:300]}")
        return self._json(path, text)

    def _headers(self, idempotency_key: str | None) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._settings.token:
            headers["Authorization"] = f"Bearer {self._settings.token}"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    @staticmethod
    def _json(path: str, text: str) -> dict[str, Any]:
        if not text:
            return {}
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CostSystemRejectedError(f"{path}: answer is not JSON") from exc
        if not isinstance(decoded, dict):
            raise CostSystemRejectedError(f"{path}: answer is not a JSON object")
        return decoded

    @staticmethod
    def _calculation_body(request: CostCalculationRequest) -> dict[str, Any]:
        spec = request.spec
        return {
            "rentalId": str(request.rental_id),
            "externalReference": request.external_reference,
            "accrualDate": request.period.day.isoformat(),
            "periodStart": request.period.start.isoformat(),
            "periodEnd": request.period.end.isoformat(),
            "shiftHoursPerDay": str(request.shift_hours_per_day),
            "mileageKm": str(request.mileage_km) if request.mileage_km is not None else None,
            "equipment": {
                "inventoryNumber": spec.inventory_number,
                "name": spec.name,
                "catalogItemId": spec.catalog_item_id,
                "purchasePrice": str(spec.purchase_price),
                "residualValue": str(spec.residual_value),
                "usefulLifeMonths": spec.useful_life_months,
                "fuelType": spec.fuel_type.value,
                "fuelConsumptionPerHour": str(spec.fuel_consumption_per_hour),
                "fuelConsumptionMode": spec.fuel_consumption_mode.value,
                "fuelConsumptionPer100Km": str(spec.fuel_consumption_per_100km),
                "registrationNumber": spec.registration_number,
                "dailyOverhead": str(spec.daily_overhead),
                "currency": spec.currency,
            },
        }

    @classmethod
    def _calculation(cls, answer: dict[str, Any]) -> CostCalculation:
        lines = answer.get("lines")
        currency = answer.get("currency")
        if not isinstance(lines, list) or not isinstance(currency, str):
            raise CostSystemRejectedError("Calculation needs 'currency' and a list of 'lines'")
        calculation_id = answer.get("calculationId")
        return CostCalculation(
            calculation_id=str(calculation_id) if calculation_id is not None else None,
            currency=currency,
            lines=tuple(cls._line(line) for line in lines),
        )

    @staticmethod
    def _line(line: Any) -> CalculatedLine:
        if not isinstance(line, dict) or not isinstance(line.get("costType"), str):
            raise CostSystemRejectedError("Every line needs a 'costType'")
        try:
            amount = Decimal(str(line["amount"]))
            quantity = Decimal(str(line["quantity"])) if line.get("quantity") is not None else None
        except (KeyError, InvalidOperation) as exc:
            raise CostSystemRejectedError("Line amounts must be decimal numbers") from exc
        if not amount.is_finite() or (quantity is not None and not quantity.is_finite()):
            raise CostSystemRejectedError("Line amounts must be finite")
        return CalculatedLine(
            cost_type=line["costType"],
            amount=amount,
            quantity=quantity,
            unit=line.get("unit"),
            description=str(line.get("description") or ""),
        )
