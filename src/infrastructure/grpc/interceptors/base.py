from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import grpc
from grpc import RpcMethodHandler
from grpc.aio import ServerInterceptor, ServicerContext

from src.infrastructure.grpc.exceptions.base import ExceptionStatusMapper

_DETAILS = {
    grpc.StatusCode.ALREADY_EXISTS: "The resource already exists",
    grpc.StatusCode.INTERNAL: "Internal error",
}


class ErrorInterceptor(ServerInterceptor):
    """Turns exceptions into gRPC statuses; expected ones keep their message."""

    def __init__(self, logger: logging.Logger, status_mapper: ExceptionStatusMapper) -> None:
        self._logger = logger
        self._status_mapper = status_mapper

    async def intercept_service(
        self,
        continuation: Callable[[grpc.HandlerCallDetails], Awaitable[RpcMethodHandler | None]],
        handler_call_details: grpc.HandlerCallDetails,
    ) -> RpcMethodHandler | None:
        handler = await continuation(handler_call_details)
        if handler is None or handler.unary_unary is None:
            return handler
        method = handler_call_details.method

        async def wrapper(request: Any, context: ServicerContext) -> Any:
            try:
                return await handler.unary_unary(request, context)
            except Exception as error:
                await self._abort(context, error, method)

        return grpc.unary_unary_rpc_method_handler(
            wrapper,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )

    async def _abort(self, context: ServicerContext, error: Exception, method: str) -> None:
        status = self._status_mapper.status(error)
        extra = {"grpc_method": method, "grpc_status": status.name}
        if status is grpc.StatusCode.INTERNAL:
            self._logger.exception("gRPC request failed", extra=extra)
        else:
            self._logger.info("gRPC request rejected", extra={**extra, "detail": str(error)})
        await context.abort(status, _DETAILS.get(status, str(error)))
