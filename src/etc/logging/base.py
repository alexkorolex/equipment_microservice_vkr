from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from logging import Handler, LogRecord
from queue import Queue
from typing import Any, cast

from logging_loki import LokiQueueHandler
from opentelemetry import trace


class JsonFormatter(logging.Formatter):
    def __init__(self, service_name: str) -> None:
        super().__init__()
        self._service_name = service_name

    def format(self, record: LogRecord) -> str:
        span_context = trace.get_current_span().get_span_context()
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": self._service_name,
            "trace_id": self._trace_id(span_context),
            "span_id": self._span_id(span_context),
        }
        payload.update(self._extras(record))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)

    @staticmethod
    def _trace_id(context: Any) -> str | None:
        if context.is_valid and context.trace_id:
            return f"{context.trace_id:032x}"
        return None

    @staticmethod
    def _span_id(context: Any) -> str | None:
        if context.is_valid and context.span_id:
            return f"{context.span_id:016x}"
        return None

    def _extras(self, record: LogRecord) -> dict[str, Any]:
        return {
            key: self._serializable(value)
            for key, value in record.__dict__.items()
            if key not in self._standard_keys() and value is not None
        }

    @staticmethod
    def _serializable(value: Any) -> Any:
        try:
            json.dumps(value)
            return value
        except (TypeError, ValueError):
            return str(value)

    @staticmethod
    def _standard_keys() -> set[str]:
        return set(logging.makeLogRecord({}).__dict__) | {
            "message",
            "asctime",
            "service",
            "trace_id",
            "span_id",
        }


class LoggingConfigurator:
    def __init__(self, service_name: str, loki_endpoint: str | None) -> None:
        self._service_name = service_name
        self._loki_endpoint = loki_endpoint
        self._loki_queue: Queue[LogRecord] = Queue(maxsize=10_000)

    def configure(self) -> logging.Logger:
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        root_logger.handlers.clear()
        formatter = JsonFormatter(self._service_name)
        root_logger.addHandler(self._console_handler(formatter))
        loki_handler = self._loki_handler(formatter)
        if loki_handler is not None:
            root_logger.addHandler(loki_handler)
        logging.getLogger("opentelemetry").setLevel(logging.WARNING)
        logging.getLogger("aiokafka").setLevel(logging.WARNING)
        return logging.getLogger(self._service_name)

    @staticmethod
    def _console_handler(formatter: logging.Formatter) -> Handler:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(formatter)
        return cast(Handler, handler)

    def _loki_handler(self, formatter: logging.Formatter) -> Handler | None:
        if not self._loki_endpoint:
            return None
        handler = LokiQueueHandler(
            queue=self._loki_queue,
            url=self._loki_endpoint,
            tags={"application": self._service_name},
            version="1",
        )
        handler.setFormatter(formatter)
        return cast(Handler, handler)
