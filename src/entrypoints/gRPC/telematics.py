from __future__ import annotations

from src.application.equipment.telematics_service import TelematicsService
from src.domain.equipment.telematics import TelematicsAccount, TelematicsCredentials, TelematicsUnit
from src.domains.equipment import equipment_pb2 as pb2
from src.domains.equipment import equipment_pb2_grpc as pb2_grpc
from src.entrypoints.gRPC.requests import RequestMapper, timestamp
from src.entrypoints.gRPC.responses import ts


def account_message(organization_id: int, account: TelematicsAccount | None) -> pb2.TelematicsAccount:
    if account is None:
        return pb2.TelematicsAccount(organizationId=organization_id, configured=False)
    return pb2.TelematicsAccount(
        organizationId=account.organization_id,
        configured=True,
        provider=account.provider,
        login=account.login,
        baseUrl=account.base_url,
        updatedByUserId=account.updated_by_user_id,
        updatedAt=ts(account.updated_at),
    )


def unit_message(unit: TelematicsUnit) -> pb2.TelematicsUnit:
    message = pb2.TelematicsUnit(
        objectId=unit.object_id,
        name=unit.name,
        imei=unit.imei,
        status=unit.status,
        latitude=unit.latitude,
        longitude=unit.longitude,
        direction=unit.direction,
        speed=unit.speed,
        lastDataAt=unit.last_data_at,
        address=unit.address,
        sensors=[
            pb2.TelematicsSensor(name=sensor.name, value=sensor.value, measuredAt=sensor.measured_at)
            for sensor in unit.sensors
        ],
    )
    if unit.equipment_id is not None:
        message.equipmentId = str(unit.equipment_id)
    return message


class TelematicsGrpcService(pb2_grpc.TelematicsServiceServicer):
    def __init__(self, service: TelematicsService) -> None:
        self._service = service

    async def SetTelematicsAccount(
        self, request: pb2.SetTelematicsAccountRequest, context: object
    ) -> pb2.TelematicsAccountResponse:
        organization_id = RequestMapper.organization(request.organizationId)
        account = await self._service.set_account(
            organization_id,
            TelematicsCredentials(request.login, request.password, request.baseUrl.strip()),
            request.actorUserId,
        )
        return pb2.TelematicsAccountResponse(data=account_message(organization_id, account))

    async def GetTelematicsAccount(
        self, request: pb2.GetTelematicsAccountRequest, context: object
    ) -> pb2.TelematicsAccountResponse:
        organization_id = RequestMapper.organization(request.organizationId)
        account = await self._service.get_account(organization_id)
        return pb2.TelematicsAccountResponse(data=account_message(organization_id, account))

    async def DeleteTelematicsAccount(
        self, request: pb2.GetTelematicsAccountRequest, context: object
    ) -> pb2.TelematicsAccountResponse:
        organization_id = RequestMapper.organization(request.organizationId)
        await self._service.delete_account(organization_id)
        return pb2.TelematicsAccountResponse(data=account_message(organization_id, None))

    async def ListTelematicsUnits(
        self, request: pb2.ListTelematicsUnitsRequest, context: object
    ) -> pb2.TelematicsUnitListResponse:
        units = await self._service.units(RequestMapper.organization(request.organizationId))
        return pb2.TelematicsUnitListResponse(data=[unit_message(unit) for unit in units])

    async def GetTelematicsUnit(
        self, request: pb2.GetTelematicsUnitRequest, context: object
    ) -> pb2.TelematicsUnitResponse:
        unit = await self._service.unit(
            RequestMapper.organization(request.organizationId), request.objectId
        )
        return pb2.TelematicsUnitResponse(data=unit_message(unit))

    async def GetTelematicsTrack(
        self, request: pb2.GetTelematicsTrackRequest, context: object
    ) -> pb2.TelematicsTrackResponse:
        track = await self._service.track(
            RequestMapper.organization(request.organizationId),
            request.objectId,
            timestamp(request, "startsAt"),
            timestamp(request, "endsAt"),
        )
        return pb2.TelematicsTrackResponse(data=pb2.TelematicsTrack(
            objectId=track.object_id,
            distanceKm=str(track.distance_km),
            points=[
                pb2.TrackPoint(at=point.at, latitude=point.latitude, longitude=point.longitude,
                               speed=point.speed)
                for point in track.points
            ],
        ))
