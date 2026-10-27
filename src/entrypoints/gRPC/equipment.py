from __future__ import annotations

from src.application.equipment.equipment_service import EquipmentService
from src.application.equipment.rental_service import RentalService
from src.domain.equipment.models import EquipmentStatus
from src.domains.equipment import equipment_pb2 as pb2
from src.domains.equipment import equipment_pb2_grpc as pb2_grpc
from src.entrypoints.gRPC import responses
from src.entrypoints.gRPC.requests import (
    RequestMapper,
    enum,
    identifier,
    optional_decimal,
    optional_timestamp,
)


class EquipmentGrpcService(pb2_grpc.EquipmentServiceServicer):
    def __init__(self, service: EquipmentService, requests: RequestMapper) -> None:
        self._service = service
        self._requests = requests

    async def CreateEquipment(
        self, request: pb2.CreateEquipmentRequest, context: object
    ) -> pb2.EquipmentResponse:
        item = await self._service.create(self._requests.spec(request), request.createdByUserId)
        return pb2.EquipmentResponse(data=responses.equipment(item))

    async def UpdateEquipment(
        self, request: pb2.UpdateEquipmentRequest, context: object
    ) -> pb2.EquipmentResponse:
        item = await self._service.update(
            self._requests.organization(request.organizationId),
            identifier(request.id, "id"),
            self._requests.changes(request),
            request.expectedVersion if request.HasField("expectedVersion") else None,
        )
        return pb2.EquipmentResponse(data=responses.equipment(item))

    async def GetEquipment(
        self, request: pb2.GetEquipmentRequest, context: object
    ) -> pb2.EquipmentResponse:
        item = await self._service.get(
            self._requests.organization(request.organizationId), identifier(request.id, "id")
        )
        return pb2.EquipmentResponse(data=responses.equipment(item))

    async def ListEquipment(
        self, request: pb2.ListEquipmentRequest, context: object
    ) -> pb2.EquipmentListResponse:
        status = enum(EquipmentStatus, request.status, "status") if request.HasField("status") else None
        items = await self._service.list(
            self._requests.page(request.organizationId, request.limit, request.offset), status
        )
        return pb2.EquipmentListResponse(data=[responses.equipment(item) for item in items])


class RentalGrpcService(pb2_grpc.RentalServiceServicer):
    def __init__(self, service: RentalService, requests: RequestMapper) -> None:
        self._service = service
        self._requests = requests

    async def CreateRental(
        self, request: pb2.CreateRentalRequest, context: object
    ) -> pb2.RentalResponse:
        rental = await self._service.open(self._requests.rental(request))
        return pb2.RentalResponse(data=responses.rental(rental))

    async def GetRental(self, request: pb2.GetRentalRequest, context: object) -> pb2.RentalResponse:
        rental = await self._service.get(
            self._requests.organization(request.organizationId), identifier(request.id, "id")
        )
        return pb2.RentalResponse(data=responses.rental(rental))

    async def ListRentals(
        self, request: pb2.ListRentalsRequest, context: object
    ) -> pb2.RentalListResponse:
        rentals = await self._service.list(self._requests.rental_filter(request))
        return pb2.RentalListResponse(data=[responses.rental(rental) for rental in rentals])

    async def ReturnEquipment(
        self, request: pb2.ReturnEquipmentRequest, context: object
    ) -> pb2.RentalResponse:
        rental = await self._service.return_equipment(
            self._requests.organization(request.organizationId),
            identifier(request.id, "id"),
            optional_timestamp(request, "returnedAt"),
            request.actorUserId,
            optional_decimal(request, "odometerEnd"),
        )
        return pb2.RentalResponse(data=responses.rental(rental))

    async def CancelRental(
        self, request: pb2.CancelRentalRequest, context: object
    ) -> pb2.RentalResponse:
        rental = await self._service.cancel(
            self._requests.organization(request.organizationId),
            identifier(request.id, "id"),
            request.actorUserId,
        )
        return pb2.RentalResponse(data=responses.rental(rental))

    async def GetRentalCosts(
        self, request: pb2.GetRentalCostsRequest, context: object
    ) -> pb2.RentalCostsResponse:
        costs = await self._service.costs(
            self._requests.organization(request.organizationId),
            identifier(request.rentalId, "rentalId"),
        )
        return pb2.RentalCostsResponse(data=responses.costs(costs))

