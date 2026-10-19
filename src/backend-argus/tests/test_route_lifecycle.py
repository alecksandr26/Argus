"""Device writes vs a route's lifecycle, and how a device finds its own route.

See docs/designs/edge-cloud-scenarios.md for the scenario IDs.
"""
from __future__ import annotations

import pytest

from app.models.common import RouteStatus, TruckStatus
from app.models.truck import Truck
from tests.conftest import auth_headers, fusion_alert_body, make_rig, status_body


@pytest.mark.parametrize("finished", [RouteStatus.COMPLETED, RouteStatus.CANCELLED])
async def test_device_alert_on_finished_route_is_409(api_client, mongo_client, finished):
    rig = await make_rig("DONE-1", route_status=finished)
    resp = await api_client.post(
        "/api/alerts", json=fusion_alert_body(rig.route_id), headers=rig.headers
    )
    assert resp.status_code == 409


@pytest.mark.parametrize("finished", [RouteStatus.COMPLETED, RouteStatus.CANCELLED])
async def test_device_status_on_finished_route_is_409(api_client, mongo_client, finished):
    rig = await make_rig("DONE-2", route_status=finished)
    resp = await api_client.post(
        f"/api/routes/{rig.route_id}/status", json=status_body(), headers=rig.headers
    )
    assert resp.status_code == 409


@pytest.mark.parametrize(
    "state", [RouteStatus.SCHEDULED, RouteStatus.IN_PROGRESS, RouteStatus.IN_PROGRESS_ALERT]
)
async def test_device_status_on_open_route_is_accepted(api_client, mongo_client, state):
    rig = await make_rig("OPEN-1", route_status=state)
    resp = await api_client.post(
        f"/api/routes/{rig.route_id}/status", json=status_body(), headers=rig.headers
    )
    assert resp.status_code == 201


async def test_jwt_can_still_post_to_completed_route(api_client, root_admin, mongo_client):
    """Manual correction path stays open for humans even after a route is finished."""
    rig = await make_rig("DONE-3", route_status=RouteStatus.COMPLETED)
    resp = await api_client.post(
        f"/api/routes/{rig.route_id}/status", json=status_body(), headers=auth_headers(root_admin)
    )
    assert resp.status_code == 201


async def test_device_active_route_returns_its_in_progress_route(api_client, rig_a, rig_b):
    resp = await api_client.get("/api/devices/me/active-route", headers=rig_a.headers)
    assert resp.status_code == 200
    assert resp.json()["id_route"] == rig_a.route_id


async def test_device_active_route_is_404_when_truck_has_none(api_client, mongo_client):
    rig = await make_rig("IDLE-1", route_status=RouteStatus.COMPLETED)
    resp = await api_client.get("/api/devices/me/active-route", headers=rig.headers)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "No active route for this truck"  # not a missing endpoint


async def test_device_active_route_rejects_a_bad_key(api_client, rig_a):
    resp = await api_client.get(
        "/api/devices/me/active-route", headers={"X-Device-Api-Key": "wrong"}
    )
    assert resp.status_code == 401


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="BE-26a: writes accept a scheduled route that /me/active-route never returns")
async def test_a_route_a_device_can_write_to_is_one_it_can_find(api_client, mongo_client):
    """The two device-facing rules must agree. `authorize_route_write` only refuses finished
    routes, so a scheduled (not yet started) route takes device writes (pinned on purpose by
    test_device_status_on_open_route_is_accepted), but `/me/active-route` only returns
    in-progress routes. A device configured for a route that hasn't started writes data Live ops
    never shows, and can't find that route itself. Fix either side: return scheduled routes from
    /me/active-route, or 409 device writes to them (and drop SCHEDULED from that other test)."""
    rig = await make_rig("SCHED-1", route_status=RouteStatus.SCHEDULED)
    write = await api_client.post(
        f"/api/routes/{rig.route_id}/status", json=status_body(), headers=rig.headers
    )
    found = await api_client.get("/api/devices/me/active-route", headers=rig.headers)
    can_write = write.status_code == 201
    can_find = found.status_code == 200 and found.json()["id_route"] == rig.route_id
    assert can_write == can_find, f"write={write.status_code} active-route={found.status_code}"


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="BE-26b (proposed policy, needs a decision): a truck out of service still ingests")
@pytest.mark.parametrize("truck_state", [TruckStatus.MAINTENANCE, TruckStatus.INACTIVE])
async def test_device_writes_for_a_truck_out_of_service_are_409(api_client, mongo_client, truck_state):
    """Proposed: a truck an operator marked `maintenance` or `inactive` shouldn't be filling a
    route with live data; its device gets 409 like a finished route (and refetches). Not decided
    yet: `Truck.operative_status` defaults to `inactive`, so enforcing this also means trucks must
    be set `active` before a trip (the simulator and seed data already do)."""
    rig = await make_rig(f"OOS-{truck_state.value}")
    truck = await Truck.get(rig.truck_id)
    truck.operative_status = truck_state
    await truck.save()
    resp = await api_client.post(
        f"/api/routes/{rig.route_id}/status", json=status_body(), headers=rig.headers
    )
    assert resp.status_code == 409
