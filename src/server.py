from __future__ import annotations

import asyncio
import logging
import signal

import aiohttp
import asyncpg
import grpc

from src.application.equipment.accrual_service import AccrualService
from src.application.equipment.equipment_service import EquipmentService
from src.application.equipment.rental_service import RentalService
from src.application.equipment.submission_service import SubmissionService
from src.application.equipment.telematics_service import TelematicsService
from src.domains.equipment import equipment_pb2_grpc
from src.entrypoints.gRPC.equipment import EquipmentGrpcService, RentalGrpcService
from src.entrypoints.gRPC.requests import RequestMapper
from src.entrypoints.gRPC.telematics import TelematicsGrpcService
from src.etc.logging.base import LoggingConfigurator
from src.etc.opentelemetry.base import TelemetryConfigurator
from src.etc.settings_microservice.base import Settings, SettingsLoader
from src.infrastructure.clock import SystemClock
from src.infrastructure.cost_system.client import HttpCostSystem
from src.infrastructure.db import Database
from src.infrastructure.db.repositories.costs import PostgresCostRepository
from src.infrastructure.db.repositories.equipment import PostgresEquipmentRepository
from src.infrastructure.db.repositories.outbox import PostgresOutboxRepository
from src.infrastructure.db.repositories.rentals import PostgresRentalRepository
from src.infrastructure.db.repositories.telematics import PostgresTelematicsAccounts
from src.infrastructure.grpc.exceptions.base import ExceptionStatusMapper
from src.infrastructure.grpc.interceptors.base import ErrorInterceptor
from src.infrastructure.telematics.crypto import SecretBox
from src.infrastructure.telematics.fortmonitor import FortMonitorProvider
from src.infrastructure.workers import PeriodicWorker


class EquipmentApplication:
    def __init__(self, settings: Settings, logger: logging.Logger) -> None:
        self._settings = settings
        self._logger = logger
        self._clock = SystemClock()
        self._stop_event = asyncio.Event()

    async def run(self) -> None:
        TelemetryConfigurator(self._settings).configure()
        async with Database(self._settings, self._logger) as database, aiohttp.ClientSession() as session:
            pool = self._required_pool(database)
            cost_system = HttpCostSystem(self._settings.cost_system, session, self._logger)
            telematics = self._telematics(pool)
            server = self._grpc_server(pool, telematics)
            workers = self._worker_tasks(pool, cost_system, telematics)
            self._install_signal_handlers(server)
            await server.start()
            self._logger.info("Equipment service started", extra={"port": self._settings.port})
            try:
                await server.wait_for_termination()
            finally:
                await self._shutdown(server, workers)

    def _telematics(self, pool: asyncpg.Pool) -> TelematicsService:
        settings = self._settings.telematics
        if settings.encryption_key is None:
            self._logger.warning("TELEMATICS_ENCRYPTION_KEY is not set: FortMonitor is disabled")
        provider = FortMonitorProvider(
            settings.default_base_url, settings.timeout_seconds, self._logger
        )
        return TelematicsService(
            PostgresTelematicsAccounts(pool), provider, SecretBox(settings.encryption_key),
            PostgresEquipmentRepository(pool), self._logger,
        )

    def _grpc_server(self, pool: asyncpg.Pool, telematics: TelematicsService) -> grpc.aio.Server:
        requests = RequestMapper(self._settings.default_shift_hours)
        rentals = RentalService(
            PostgresRentalRepository(pool), PostgresCostRepository(pool), self._clock, self._logger
        )
        server = grpc.aio.server(
            interceptors=[ErrorInterceptor(self._logger, ExceptionStatusMapper())]
        )
        server.add_insecure_port(f"[::]:{self._settings.port}")
        equipment_pb2_grpc.add_EquipmentServiceServicer_to_server(
            EquipmentGrpcService(
                EquipmentService(PostgresEquipmentRepository(pool), self._logger), requests
            ),
            server,
        )
        equipment_pb2_grpc.add_RentalServiceServicer_to_server(
            RentalGrpcService(rentals, requests), server
        )
        equipment_pb2_grpc.add_TelematicsServiceServicer_to_server(
            TelematicsGrpcService(telematics), server
        )
        return server

    def _worker_tasks(
        self, pool: asyncpg.Pool, cost_system: HttpCostSystem, telematics: TelematicsService
    ) -> tuple[asyncio.Task[None], ...]:
        settings = self._settings.workers
        submission = SubmissionService(
            PostgresOutboxRepository(pool), cost_system, self._clock, self._logger,
            settings.batch_size, settings.max_submit_attempts,
        )
        accrual = AccrualService(
            PostgresCostRepository(pool), cost_system, self._clock, self._logger,
            settings.batch_size, mileage=telematics,
        )
        workers = (
            PeriodicWorker("rental-submission", submission.run_once,
                           settings.submit_interval_seconds, self._logger),
            PeriodicWorker("cost-accrual", accrual.run_once,
                           settings.accrual_interval_seconds, self._logger),
        )
        return tuple(
            asyncio.create_task(worker.run(self._stop_event), name=f"equipment-{index}")
            for index, worker in enumerate(workers)
        )

    def _install_signal_handlers(self, server: grpc.aio.Server) -> None:
        loop = asyncio.get_running_loop()

        def stop() -> None:
            self._stop_event.set()
            loop.create_task(server.stop(grace=10))

        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(signum, stop)

    async def _shutdown(
        self, server: grpc.aio.Server, workers: tuple[asyncio.Task[None], ...]
    ) -> None:
        self._stop_event.set()
        await asyncio.gather(*workers, return_exceptions=True)
        await server.stop(grace=10)
        self._logger.info("Equipment service stopped")

    @staticmethod
    def _required_pool(database: Database) -> asyncpg.Pool:
        if database.pool is None:
            raise RuntimeError("Database pool is unavailable")
        return database.pool


async def serve() -> None:
    settings = SettingsLoader().load()
    logger = LoggingConfigurator(settings.service_name, settings.loki_endpoint).configure()
    await EquipmentApplication(settings, logger).run()
