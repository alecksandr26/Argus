"""Device writes vs a route's lifecycle, and how a device finds its own route.

See docs/designs/edge-cloud-scenarios.md for the scenario IDs.
"""
from __future__ import annotations

import pytest

from app.models.common import RouteStatus
from tests.conftest import auth_headers, fusion_alert_body, make_rig, status_body


@pytest.mark.xfail(strict=True, reason="BE-04: devices can write to finished routes")
@pytest.mark.parametrize("finished", [RouteStatus.COMPLETED, RouteStatus.CANCELLED])
async def test_device_alert_on_finished_route_is_409(api_client, mongo_client, finished):
    rig = await make_rig("DONE-1", route_status=finished)
    resp = await api_client.post(
        "/api/alerts", json=fusion_alert_body(rig.route_id), headers=rig.headers
    )
    assert resp.status_code == 409


@pytest.mark.xfail(strict=True, reason="BE-04: devices can write to finished routes")
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


@pytest.mark.xfail(strict=True, reason="BE-05: no device active-route endpoint; ROUTE_ID hardcoded")
async def test_device_active_route_returns_its_in_progress_route(api_client, rig_a, rig_b):
    resp = await api_client.get("/api/devices/me/active-route", headers=rig_a.headers)
    assert resp.status_code == 200
    assert resp.json()["id_route"] == rig_a.route_id


@pytest.mark.xfail(strict=True, reason="BE-05: no device active-route endpoint; ROUTE_ID hardcoded")
async def test_device_active_route_is_404_when_truck_has_none(api_client, mongo_client):
    rig = await make_rig("IDLE-1", route_status=RouteStatus.COMPLETED)
    resp = await api_client.get("/api/devices/me/active-route", headers=rig.headers)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "No active route for this truck"  # not a missing endpoint


@pytest.mark.xfail(strict=True, reason="BE-05: no device active-route endpoint; ROUTE_ID hardcoded")
async def test_device_active_route_rejects_a_bad_key(api_client, rig_a):
    resp = await api_client.get(
        "/api/devices/me/active-route", headers={"X-Device-Api-Key": "wrong"}
    )
    assert resp.status_code == 401
