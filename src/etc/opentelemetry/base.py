import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.grpc import (
    GrpcAioInstrumentorClient,
    GrpcAioInstrumentorServer,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from src.etc.settings_microservice.base import Settings


class TelemetryConfigurator:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def configure(self) -> None:
        provider = TracerProvider(resource=self._resource())
        exporter = OTLPSpanExporter(
            endpoint=self._endpoint(),
            timeout=30,
        )
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        GrpcAioInstrumentorServer().instrument(tracer_provider=provider)
        GrpcAioInstrumentorClient().instrument(tracer_provider=provider)

    def _resource(self) -> Resource:
        return Resource.create(
            {
                "service.name": self._settings.service_name,
                "service.version": "1.0.0",
                "deployment.environment": os.getenv("ENV", "dev"),
            }
        )

    def _endpoint(self) -> str:
        return (
            f"http://{self._settings.otel_host}:"
            f"{self._settings.otel_port}/v1/traces"
        )
