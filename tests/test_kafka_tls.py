from __future__ import annotations

import logging
import ssl
import subprocess
from pathlib import Path

import pytest

from src.infrastructure.kafka.tls import KafkaTlsContextFactory


def test_plaintext_requires_explicit_opt_in() -> None:
    factory = KafkaTlsContextFactory({"KAFKA_SECURITY_PROTOCOL": "PLAINTEXT"}, logging.getLogger("test"))
    assert factory.build() is None


def test_invalid_protocol_is_rejected() -> None:
    with pytest.raises(ValueError, match="KAFKA_SECURITY_PROTOCOL"):
        KafkaTlsContextFactory({"KAFKA_SECURITY_PROTOCOL": "SASL_PLAINTEXT"}, logging.getLogger("test")).build()


def test_missing_tls_material_does_not_fall_back_to_plaintext(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        KafkaTlsContextFactory({"KAFKA_SSL_CA_FILE": str(tmp_path / "missing.crt")}, logging.getLogger("test")).build()


def test_context_loads_identity_and_verifies_broker(tmp_path: Path) -> None:
    certificate, key = tmp_path / "client.crt", tmp_path / "client.key"
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
        "-subj", "/CN=test", "-keyout", str(key), "-out", str(certificate),
    ], check=True, capture_output=True)
    security = KafkaTlsContextFactory({
        "KAFKA_SSL_CA_FILE": str(certificate), "KAFKA_SSL_CERT_FILE": str(certificate),
        "KAFKA_SSL_KEY_FILE": str(key),
    }, logging.getLogger("test")).build()
    assert security is not None and security.use_ssl
    context = security.ssl_context
    assert context is not None
    assert context.check_hostname
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2
