"""Leases between organizations and the dispatcher's view, through gRPC on a real PostgreSQL."""

from __future__ import annotations

import asyncpg
import grpc
import pytest

from src.domains.equipment import equipment_pb2 as pb2
from tests.common import at
from tests.test_grpc_flow import Harness, harness, ts, tractor  # noqa: F401  (fixture)

pytestmark = pytest.mark.integration

OWNER, LESSEE, OTHER = 15, 16, 17


async def status_of(call: object) -> grpc.StatusCode:
    with pytest.raises(grpc.aio.AioRpcError) as error:
        await call  # type: ignore[misc]
    return error.value.code()


def rental(organization_id: int, equipment_id: str, start: int, end: int) -> pb2.CreateRentalRequest:
    return pb2.CreateRentalRequest(
        organizationId=organization_id, equipmentId=equipment_id, workerUserId=40,
        startsAt=ts(at(start, 8)), endsAt=ts(at(end, 18)), createdByUserId=40,
    )


async def test_leased_unit_is_used_by_the_lessee_and_seen_by_both(
    harness: Harness, pool: asyncpg.Pool
) -> None:
    unit = (await harness.equipment.CreateEquipment(tractor(territoryId=5))).data
    assert unit.territoryId == 5
    lease = (await harness.leases.CreateLease(pb2.CreateLeaseRequest(
        equipmentId=unit.id, ownerOrganizationId=OWNER, lesseeOrganizationId=LESSEE,
        endsAt=ts(at(10)), note="Уборка", createdByUserId=900,
    ))).data
    assert (lease.status, lease.equipment.id) == ("active", unit.id)

    # The owner cannot rent it out now, nobody else can lease it again.
    assert await status_of(harness.rentals.CreateRental(rental(OWNER, unit.id, 2, 3))) == (
        grpc.StatusCode.FAILED_PRECONDITION)
    assert await status_of(harness.leases.CreateLease(pb2.CreateLeaseRequest(
        equipmentId=unit.id, ownerOrganizationId=OWNER, lesseeOrganizationId=OTHER,
        startsAt=ts(at(5)), endsAt=ts(at(6)), createdByUserId=900,
    ))) == grpc.StatusCode.FAILED_PRECONDITION
    assert await status_of(harness.rentals.CreateRental(rental(OTHER, unit.id, 2, 3))) == (
        grpc.StatusCode.NOT_FOUND)

    # The lessee reads the unit and rents it within the lease only.
    seen = await harness.equipment.GetEquipment(
        pb2.GetEquipmentRequest(organizationId=LESSEE, id=unit.id))
    assert seen.data.organizationId == OWNER
    assert await status_of(harness.rentals.CreateRental(rental(LESSEE, unit.id, 9, 12))) == (
        grpc.StatusCode.FAILED_PRECONDITION)
    taken = (await harness.rentals.CreateRental(rental(LESSEE, unit.id, 1, 4))).data
    assert (taken.organizationId, taken.ownerOrganizationId, taken.leaseId) == (
        LESSEE, OWNER, lease.id)

    # Both organizations see the rental; a third one does not.
    for organization_id in (OWNER, LESSEE):
        listed = (await harness.rentals.ListRentals(
            pb2.ListRentalsRequest(organizationId=organization_id))).data
        assert [item.id for item in listed] == [taken.id]
        await harness.rentals.GetRentalCosts(
            pb2.GetRentalCostsRequest(organizationId=organization_id, rentalId=taken.id))
    assert await status_of(harness.rentals.GetRental(
        pb2.GetRentalRequest(organizationId=OTHER, id=taken.id))) == grpc.StatusCode.NOT_FOUND

    # The dispatcher's view: by territory and by the company's selection.
    def dispatch(territories: list[int], selection: pb2.EquipmentSelection) -> object:
        return harness.equipment.ListDispatchEquipment(pb2.ListDispatchEquipmentRequest(
            selections=[selection], territoryIds=territories))

    items = (await dispatch([5], pb2.EquipmentSelection(
        organizationId=OWNER, allEquipment=True))).data  # type: ignore[attr-defined]
    assert len(items) == 1
    assert (items[0].currentRental.id, items[0].activeLease.id) == (taken.id, lease.id)
    assert not (await dispatch([6], pb2.EquipmentSelection(
        organizationId=OWNER, allEquipment=True))).data  # type: ignore[attr-defined]
    assert not (await dispatch([5], pb2.EquipmentSelection(
        organizationId=LESSEE, allEquipment=True))).data  # type: ignore[attr-defined]
    picked = (await dispatch([5], pb2.EquipmentSelection(
        organizationId=OWNER, equipmentIds=[unit.id]))).data  # type: ignore[attr-defined]
    assert [item.equipment.id for item in picked] == [unit.id]

    # The lease cannot end under an open rental; a forced close needs a reason.
    assert await status_of(harness.leases.EndLease(pb2.EndLeaseRequest(
        id=lease.id, actorUserId=900))) == grpc.StatusCode.FAILED_PRECONDITION
    harness.clock.current = at(2, 12)
    assert await status_of(harness.rentals.ReturnEquipment(pb2.ReturnEquipmentRequest(
        organizationId=LESSEE, id=taken.id, actorUserId=900, forced=True,
    ))) == grpc.StatusCode.INVALID_ARGUMENT
    closed = (await harness.rentals.ReturnEquipment(pb2.ReturnEquipmentRequest(
        organizationId=LESSEE, id=taken.id, actorUserId=900, forced=True,
        closeReason="Техника нужна на другом объекте",
    ))).data
    assert (closed.status, closed.forceClosed, closed.closedByUserId) == ("returned", True, 900)
    assert closed.closeReason == "Техника нужна на другом объекте"
    assert closed.returnedAt.ToDatetime().replace(tzinfo=at(1).tzinfo) == at(2, 12)

    ended = (await harness.leases.EndLease(pb2.EndLeaseRequest(
        id=lease.id, actorUserId=900, reason="Работы завершены"))).data
    assert (ended.status, ended.endedByUserId) == ("ended", 900)
    assert await status_of(harness.rentals.CreateRental(rental(LESSEE, unit.id, 5, 6))) == (
        grpc.StatusCode.NOT_FOUND)
    (await harness.rentals.CreateRental(rental(OWNER, unit.id, 5, 6)))

    for side, organization_id in (("owner", OWNER), ("lessee", LESSEE)):
        leases = (await harness.leases.ListLeases(pb2.ListLeasesRequest(
            organizationId=organization_id, side=side))).data
        assert [item.id for item in leases] == [lease.id]

    # Live updates reach both organizations.
    rows = await pool.fetch(
        """SELECT DISTINCT entity, organization_id FROM equipment.change_events
        WHERE entity IN ('lease', 'rental') AND entity_id IN ($1, $2)""",
        lease.id, taken.id,
    )
    assert {(row["entity"], row["organization_id"]) for row in rows} == {
        ("lease", OWNER), ("lease", LESSEE), ("rental", OWNER), ("rental", LESSEE)}


async def test_lease_needs_a_free_unit_and_a_future_end(harness: Harness) -> None:
    unit = (await harness.equipment.CreateEquipment(tractor())).data
    await harness.rentals.CreateRental(rental(OWNER, unit.id, 2, 4))
    assert await status_of(harness.leases.CreateLease(pb2.CreateLeaseRequest(
        equipmentId=unit.id, ownerOrganizationId=OWNER, lesseeOrganizationId=LESSEE,
        endsAt=ts(at(10)), createdByUserId=900,
    ))) == grpc.StatusCode.FAILED_PRECONDITION
    assert await status_of(harness.leases.CreateLease(pb2.CreateLeaseRequest(
        equipmentId=unit.id, ownerOrganizationId=OWNER, lesseeOrganizationId=OWNER,
        startsAt=ts(at(6)), endsAt=ts(at(10)), createdByUserId=900,
    ))) == grpc.StatusCode.INVALID_ARGUMENT
    assert await status_of(harness.leases.CreateLease(pb2.CreateLeaseRequest(
        equipmentId=unit.id, ownerOrganizationId=LESSEE, lesseeOrganizationId=OWNER,
        startsAt=ts(at(6)), endsAt=ts(at(10)), createdByUserId=900,
    ))) == grpc.StatusCode.NOT_FOUND
    later = (await harness.leases.CreateLease(pb2.CreateLeaseRequest(
        equipmentId=unit.id, ownerOrganizationId=OWNER, lesseeOrganizationId=LESSEE,
        startsAt=ts(at(6)), endsAt=ts(at(10)), createdByUserId=900,
    ))).data
    assert later.status == "scheduled"
