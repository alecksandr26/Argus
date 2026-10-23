"""Device writes vs a route's lifecycle, and how a device finds its own route.

See docs/designs/edge-cloud-scenarios.md for the scenario IDs.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.common import RouteStatus, TruckStatus
from app.models.incident import DriverOk, Incident
from app.models.route import Route
from app.models.truck import Truck
from tests.conftest import (
    auth_headers,
    fusion_alert_body,
    make_rig,
    ok_event,
    post_report,
    report_event,
    status_body,
)


def _utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


# --- Stop route (C3.6) --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "state", [RouteStatus.SCHEDULED, RouteStatus.IN_PROGRESS, RouteStatus.IN_PROGRESS_ALERT]
)
async def test_stop_route(api_client, admin_user, mongo_client, state):
    rig = await make_rig(f"STOP-{state.value}", route_status=state)
    before = datetime.now(timezone.utc)
    resp = await api_client.post(
        f"/api/routes/{rig.route_id}/stop",
        json={"reason": "  truck broke down  "},
        headers=auth_headers(admin_user),
    )
    body = resp.json()
    assert resp.status_code == 200, resp.text
    assert body["operative_status"] == "cancelled"
    assert body["stop_reason"] == "truck broke down"
    assert body["stopped_by"] == str(admin_user.id)
    assert _utc(body["stopped_at"]) >= before - timedelta(seconds=1)
    if state == RouteStatus.SCHEDULED:
        assert body["actual_arrival"] is None  # never departed
    else:
        assert _utc(body["actual_arrival"]) >= before - timedelta(seconds=1)


@pytest.mark.parametrize("finished", [RouteStatus.COMPLETED, RouteStatus.CANCELLED])
async def test_stop_finished_route_is_409(api_client, root_admin, mongo_client, finished):
    rig = await make_rig("STOP-DONE", route_status=finished)
    resp = await api_client.post(
        f"/api/routes/{rig.route_id}/stop", json={"reason": "x"}, headers=auth_headers(root_admin)
    )
    assert resp.status_code == 409


@pytest.mark.parametrize("reason", ["", "   ", "x" * 501], ids=["empty", "blank", "too_long"])
async def test_stop_needs_a_reason(api_client, root_admin, rig_a, reason):
    resp = await api_client.post(
        f"/api/routes/{rig_a.route_id}/stop", json={"reason": reason}, headers=auth_headers(root_admin)
    )
    assert resp.status_code == 422
    assert (await Route.get(rig_a.route_id)).operative_status == RouteStatus.IN_PROGRESS


async def test_stop_roles(api_client, guardian, truck_driver_user, rig_a):
    url = f"/api/routes/{rig_a.route_id}/stop"
    assert (await api_client.post(url, json={"reason": "x"})).status_code == 401
    assert (await api_client.post(url, json={"reason": "x"}, headers=rig_a.headers)).status_code == 401
    for user in (guardian, truck_driver_user):
        resp = await api_client.post(url, json={"reason": "x"}, headers=auth_headers(user))
        assert resp.status_code == 403


async def test_stop_unknown_route_is_404(api_client, root_admin):
    resp = await api_client.post(
        "/api/routes/64b0a00000000000000000aa/stop", json={"reason": "x"}, headers=auth_headers(root_admin)
    )
    assert resp.status_code == 404


async def test_stop_keeps_open_incidents_open(api_client, root_admin, rig_a):
    await post_report(api_client, rig_a, event=report_event(age_s=5))
    await api_client.post(
        f"/api/routes/{rig_a.route_id}/stop", json={"reason": "x"}, headers=auth_headers(root_admin)
    )
    incidents = await Incident.find(Incident.id_route == rig_a.route_id).to_list()
    assert len(incidents) == 1 and incidents[0].closed_at is None


# --- Actual departure / arrival -----------------------------------------------------------------


async def test_start_and_complete_trip_set_actual_times(api_client, root_admin, mongo_client):
    rig = await make_rig("TRIP-1", route_status=RouteStatus.SCHEDULED)
    url = f"/api/routes/{rig.route_id}"
    headers = auth_headers(root_admin)
    started = (await api_client.put(url, json={"operative_status": "in_progress"}, headers=headers)).json()
    assert started["actual_departure"] is not None
    assert started["actual_arrival"] is None
    again = (await api_client.put(url, json={"operative_status": "in_progress_alert"}, headers=headers)).json()
    assert again["actual_departure"] == started["actual_departure"]  # kept, not reset
    done = (await api_client.put(url, json={"operative_status": "completed"}, headers=headers)).json()
    assert done["actual_arrival"] is not None
    assert done["actual_departure"] == started["actual_departure"]


async def test_explicit_actual_times_win(api_client, root_admin, mongo_client):
    rig = await make_rig("TRIP-2", route_status=RouteStatus.SCHEDULED)
    resp = await api_client.put(
        f"/api/routes/{rig.route_id}",
        json={"operative_status": "in_progress", "actual_departure": "2030-01-01T08:00:00Z"},
        headers=auth_headers(root_admin),
    )
    assert resp.json()["actual_departure"].startswith("2030-01-01T08:00:00")


async def test_route_put_does_not_overwrite_incident_state(api_client, root_admin, rig_a):
    await post_report(api_client, rig_a, event=report_event(age_s=5))
    resp = await api_client.put(
        f"/api/routes/{rig_a.route_id}", json={"origin_name": "Tepic"}, headers=auth_headers(root_admin)
    )
    assert resp.json()["origin_name"] == "Tepic"
    assert resp.json()["incident_state"] == "critical"


async def test_delete_route_cascades_incidents_and_driver_oks(api_client, root_admin, rig_a):
    await post_report(api_client, rig_a, event=report_event(age_s=5))
    await post_report(api_client, rig_a, event=ok_event(age_s=1))
    resp = await api_client.delete(f"/api/routes/{rig_a.route_id}", headers=auth_headers(root_admin))
    assert resp.status_code == 200
    assert await Incident.find_all().count() == 0
    assert await DriverOk.find_all().count() == 0


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
