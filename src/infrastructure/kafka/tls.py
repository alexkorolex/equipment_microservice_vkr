"""Kafka transport security; certificates are mounted separately for each service."""

from __future__ import annotations

import logging
import ssl
from collections.abc import Mapping

from faststream.security import BaseSecurity


class KafkaTlsContextFactory:
    def __init__(self, environment: Mapping[str, str], logger: logging.Logger) -> None:
        self._environment = environment
        self._logger = logger

    def build(self) -> BaseSecurity | None:
        protocol = self._environment.get("KAFKA_SECURITY_PROTOCOL", "SSL")
        if protocol == "PLAINTEXT":
            self._logger.warning("Kafka TLS explicitly disabled")
            return None
        if protocol != "SSL":
            raise ValueError("KAFKA_SECURITY_PROTOCOL must be SSL or PLAINTEXT")
        context = ssl.create_default_context(cafile=self._path("KAFKA_SSL_CA_FILE", "kafka-ca.crt"))
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(
            certfile=self._path("KAFKA_SSL_CERT_FILE", "kafka-client.crt"),
            keyfile=self._path("KAFKA_SSL_KEY_FILE", "kafka-client.key"),
            password="",
        )
        self._logger.info("Kafka mTLS configured", extra={"security_protocol": protocol})
        return BaseSecurity(ssl_context=context)

    def _path(self, variable: str, filename: str) -> str:
        value = self._environment.get(variable, f"/run/secrets/{filename}")
        if not value:
            raise ValueError(f"{variable} must not be empty")
        return value
