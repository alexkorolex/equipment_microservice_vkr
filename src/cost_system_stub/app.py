"""A stand-in for the external cost system: same HTTP API, simple formulas, loud logs."""

from __future__ import annotations

import logging
import random
from collections.abc import Awaitable, Callable
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import uuid4

from aiohttp import web

from src.cost_system_stub.calculator import CostCalculator
from src.cost_system_stub.settings import StubSettings

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


class CostSystemStub:
    def __init__(
        self,
        calculator: CostCalculator,
        settings: StubSettings,
        logger: logging.Logger,
        chance: Callable[[], float] = random.random,
    ) -> None:
        self._calculator = calculator
        self._settings = settings
        self._logger = logger
        self._chance = chance
        self._rentals: dict[str, dict[str, Any]] = {}

    def application(self) -> web.Application:
        app = web.Application(middlewares=[self._failures, self._bad_requests])
        app.router.add_get("/health", self.health)
        app.router.add_post("/v1/rentals", self.register)
        app.router.add_post("/v1/rentals/{rental_id}/return", self.close)
        app.router.add_post("/v1/rentals/{rental_id}/cancel", self.close)
        app.router.add_post("/v1/cost-calculations", self.calculate)
        return app

    async def health(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def register(self, request: web.Request) -> web.Response:
        body = await request.json()
        rental_id, equipment = str(body["rentalId"]), body["equipment"]
        reference = f"EXT-{rental_id[:8].upper()}"
        self._rentals[rental_id] = {**body, "reference": reference, "status": "open"}
        self._logger.info(
            "Stub: rental registered",
            extra={"rental_id": rental_id, "reference": reference,
                   "inventory_number": equipment.get("inventoryNumber"),
                   "worker_user_id": body.get("workerUserId")},
        )
        return web.json_response({"reference": reference}, status=201)

    async def close(self, request: web.Request) -> web.Response:
        rental_id = request.match_info["rental_id"]
        action = request.path.rsplit("/", 1)[-1]
        known = rental_id in self._rentals
        if known:
            self._rentals[rental_id]["status"] = action
        self._logger.info(
            f"Stub: rental {action}", extra={"rental_id": rental_id, "known": known}
        )
        return web.json_response({"status": action})

    async def calculate(self, request: web.Request) -> web.Response:
        body = await request.json()
        start = datetime.fromisoformat(body["periodStart"])
        end = datetime.fromisoformat(body["periodEnd"])
        hours = Decimal((end - start).total_seconds()) / Decimal(3600)
        lines = self._calculator.calculate(
            hours, Decimal(body["shiftHoursPerDay"]), body["equipment"]
        )
        calculation_id = f"CALC-{uuid4().hex[:12].upper()}"
        self._logger.info(
            "Stub: costs calculated",
            extra={"rental_id": body["rentalId"], "accrual_date": body["accrualDate"],
                   "calculation_id": calculation_id, "hours": str(hours.quantize(Decimal("0.01"))),
                   "total": str(sum((line.amount for line in lines), Decimal(0))),
                   "idempotency_key": request.headers.get("Idempotency-Key")},
        )
        return web.json_response({
            "calculationId": calculation_id,
            "currency": body["equipment"]["currency"],
            "lines": [line.to_json() for line in lines],
        })

    @web.middleware
    async def _failures(self, request: web.Request, handler: Handler) -> web.StreamResponse:
        """Fail like a flaky real system (``STUB_FAILURE_RATE``) to exercise retries."""
        if request.path != "/health" and self._chance() < self._settings.failure_rate:
            self._logger.warning("Stub: simulated outage", extra={"path": request.path})
            return web.json_response({"error": "simulated outage"}, status=503)
        return await handler(request)

    @web.middleware
    async def _bad_requests(self, request: web.Request, handler: Handler) -> web.StreamResponse:
        try:
            return await handler(request)
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            self._logger.warning("Stub: invalid request", extra={"path": request.path, "error": repr(exc)})
            return web.json_response({"error": f"invalid request: {exc!r}"}, status=400)
