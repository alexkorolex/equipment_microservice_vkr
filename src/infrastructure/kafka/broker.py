"""The service's Kafka broker (FastStream over aiokafka).

Every message gets an OpenTelemetry span that continues the producer's trace and
Prometheus metrics (received, processed, failed, duration) per topic; the offset is
committed only after the handler finished.
"""

from __future__ import annotations

import logging

from faststream import AckPolicy
from faststream.kafka import KafkaBroker
from faststream.kafka.opentelemetry import KafkaTelemetryMiddleware
from faststream.kafka.prometheus import KafkaPrometheusMiddleware
from prometheus_client import REGISTRY, start_http_server


def create_broker(bootstrap_servers: str, service_name: str, logger: logging.Logger) -> KafkaBroker:
    # aiokafka is chatty at INFO (connections, rebalances); its warnings stay.
    logging.getLogger("aiokafka").setLevel(logging.WARNING)
    return KafkaBroker(
        bootstrap_servers,
        client_id=service_name,
        # A failed message is read again; handlers decide what is worth retrying
        # (see errors.dead_letter_policy).
        ack_policy=AckPolicy.NACK_ON_ERROR,
        middlewares=[
            KafkaTelemetryMiddleware(),
            KafkaPrometheusMiddleware(registry=REGISTRY, app_name=service_name),
        ],
        logger=logger,
        # "Received"/"Processed" for every message: debug only, failures stay errors.
        log_level=logging.DEBUG,
    )


def start_metrics_server(port: int) -> None:
    """Prometheus scrapes /metrics here: the service has no HTTP server of its own."""
    start_http_server(port)
