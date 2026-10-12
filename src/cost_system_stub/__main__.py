from aiohttp import web

from src.cost_system_stub.app import CostSystemStub
from src.cost_system_stub.calculator import CostCalculator
from src.cost_system_stub.settings import StubSettings
from src.etc.logging.base import LoggingConfigurator


def main() -> None:
    settings = StubSettings.from_env()
    logger = LoggingConfigurator(settings.service_name, settings.loki_endpoint).configure()
    stub = CostSystemStub(CostCalculator(settings.fuel_prices), settings, logger)
    logger.info("Cost system stub started", extra={"port": settings.port})
    web.run_app(stub.application(), port=settings.port, print=None, access_log=None)


if __name__ == "__main__":
    main()
