"""After a route ends, the backend takes nothing more from the device for it
(docs/designs/final-tweaks-contracts.md, C2.2 rule 4 [sync 2]):

- an event that happened after the truck's last route ended is `rejected route_finished`; one
  from before the truck had started any route is `rejected no_route`;
- a stop/complete that lands between the route lookup and the write (the route's status is
  re-read under the per-route lock) rejects the event and stores no points.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import app.report as report_module
import app.routers.alerts as alerts_router
import app.routers.status_routes as status_router
from app.models.alert import Alert
from app.models.common import RouteStatus
from app.models.incident import DriverOk, Incident
from app.models.route import Route
from app.models.status_route import StatusRoute
from tests.conftest import (
    auth_headers,
    fusion_alert_body,
    make_rig,
    ok_event,
    post_report,
    report_event,
    report_point,
    status_body,
)

REJECTED_FINISHED = {"status": "rejected", "reason": "route_finished", "id_alert": None}
REJECTED_NO_ROUTE = {"status": "rejected", "reason": "no_route", "id_alert": None}


async def _cancel(route_id: str) -> None:
    """What `POST /stop` writes, done directly (the race tests run it mid-request)."""
    now = datetime.now(timezone.utc)
    await Route.get_motor_collection().update_one(
        {"_id": (await Route.get(route_id)).id},
        {"$set": {"operative_status": RouteStatus.CANCELLED.value, "actual_arrival": now}},
    )


# --- C2.2 rule 4: no route contains occurred_at -------------------------------------------------


async def test_ok_and_alert_after_complete_trip_are_route_finished(api_client, root_admin, rig_a):
    """The driver's OK (and an alert) whose age puts them after "Complete trip"."""
    done = await api_client.put(
        f"/api/routes/{rig_a.route_id}",
        json={"operative_status": "completed"},
        headers=auth_headers(root_admin),
    )
    assert done.status_code == 200
    # Both happened just now, after the arrival "Complete trip" stamped.
    ok = (await post_report(api_client, rig_a, event=ok_event("ok-late", age_s=0))).json()
    alert = (await post_report(api_client, rig_a, event=report_event("al-late", age_s=0))).json()
    assert ok["event"] == REJECTED_FINISHED
    assert alert["event"] == REJECTED_FINISHED
    assert await Alert.find_all().count() == 0
    assert await DriverOk.find_all().count() == 0


async def test_events_before_any_route_departed_are_no_route(api_client, mongo_client):
    """The truck's only route departed an hour ago; events from two hours ago match nothing,
    and no earlier route exists."""
    rig = await make_rig("EARLY-1", departed_ago=timedelta(hours=1))
    alert = (await post_report(api_client, rig, event=report_event("e", age_s=7200))).json()
    ok = (await post_report(api_client, rig, event=ok_event("o", age_s=7200))).json()
    assert alert["event"] == REJECTED_NO_ROUTE
    assert ok["event"] == REJECTED_NO_ROUTE


async def test_truck_that_never_started_a_route_gets_no_route(api_client, mongo_client):
    rig = await make_rig("SCHED-9", route_status=RouteStatus.SCHEDULED)
    body = (await post_report(api_client, rig, event=report_event(age_s=1))).json()
    assert body["event"] == REJECTED_NO_ROUTE


async def test_after_a_finished_route_and_before_the_next_started(api_client, mongo_client):
    """Newest route that had departed by `occurred_at` decides: finished -> route_finished,
    even though a newer route (departed later) is in progress now."""
    rig = await make_rig("GAP-1", route_status=RouteStatus.COMPLETED, departed_ago=timedelta(hours=3))
    old = await Route.get(rig.route_id)
    now = datetime.now(timezone.utc)
    await old.set({"actual_arrival": now - timedelta(hours=2)})
    current = Route(**old.model_dump(exclude={"id", "revision_id"}))
    current.operative_status = RouteStatus.IN_PROGRESS
    current.actual_departure = now - timedelta(minutes=30)
    current.actual_arrival = None
    await current.insert()
    gap = (await post_report(api_client, rig, event=ok_event("gap", age_s=3600))).json()
    live = (await post_report(api_client, rig, event=report_event("now", age_s=1))).json()
    assert gap["event"] == REJECTED_FINISHED
    assert live["event"]["status"] == "created"


# --- C2.2: status re-read under the per-route lock ----------------------------------------------


@pytest.fixture
def stop_after_route_at(monkeypatch):
    """The route is cancelled right after `route_at()` found it (a stop racing the report)."""
    original = report_module.route_at

    async def racing(truck_id, occurred_at):
        route = await original(truck_id, occurred_at)
        if route is not None:
            await _cancel(str(route.id))
        return route

    monkeypatch.setattr(report_module, "route_at", racing)


async def test_alert_racing_a_stop_is_route_finished(api_client, rig_a, stop_after_route_at):
    body = (await post_report(api_client, rig_a, event=report_event("race-1"))).json()
    assert body["event"] == REJECTED_FINISHED
    assert await Alert.find_all().count() == 0
    assert await Incident.find_all().count() == 0
    route = await Route.get(rig_a.route_id)
    assert route.incident_state == "fine"
    assert route.lock_until is None  # the lock was released on the way out


async def test_ok_racing_a_stop_is_route_finished(api_client, rig_a, stop_after_route_at):
    body = (await post_report(api_client, rig_a, event=ok_event("race-ok"))).json()
    assert body["event"] == REJECTED_FINISHED
    assert await DriverOk.find_all().count() == 0


async def test_points_racing_a_stop_are_not_stored(api_client, rig_a, monkeypatch):
    original = report_module.active_route_for

    async def racing(truck_id):
        route = await original(truck_id)
        if route is not None:
            await _cancel(str(route.id))
        return route

    monkeypatch.setattr(report_module, "active_route_for", racing)
    body = (await post_report(api_client, rig_a, points=[report_point(), report_point(age_s=3)])).json()
    assert body["points"] == {"stored": 0, "route": None, "status": "route_finished"}
    assert body["active_route"] is None
    assert await StatusRoute.find_all().count() == 0


async def _cancel_after(monkeypatch, module, name):
    original = getattr(module, name)

    async def racing(route, *args, **kwargs):
        result = await original(route, *args, **kwargs)
        await _cancel(str(route.id))
        return result

    monkeypatch.setattr(module, name, racing)


async def test_legacy_device_alert_racing_a_stop_is_409(api_client, rig_a, monkeypatch):
    await _cancel_after(monkeypatch, alerts_router, "authorize_route_write")
    resp = await api_client.post(
        "/api/alerts", json=fusion_alert_body(rig_a.route_id), headers=rig_a.headers
    )
    assert resp.status_code == 409
    assert await Alert.find_all().count() == 0


async def test_legacy_device_status_racing_a_stop_is_409(api_client, rig_a, monkeypatch):
    await _cancel_after(monkeypatch, status_router, "authorize_route_write")
    resp = await api_client.post(
        f"/api/routes/{rig_a.route_id}/status", json=status_body(), headers=rig_a.headers
    )
    assert resp.status_code == 409
    assert await StatusRoute.find_all().count() == 0


async def test_user_manual_write_to_a_finished_route_still_works(api_client, root_admin, rig_a):
    """The in-lock re-check is for devices only; humans keep their correction path."""
    await _cancel(rig_a.route_id)
    alert = await api_client.post(
        "/api/alerts", json=fusion_alert_body(rig_a.route_id), headers=auth_headers(root_admin)
    )
    point = await api_client.post(
        f"/api/routes/{rig_a.route_id}/status", json=status_body(), headers=auth_headers(root_admin)
    )
    assert (alert.status_code, point.status_code) == (201, 201)
