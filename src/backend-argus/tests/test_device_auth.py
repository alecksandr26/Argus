"""Device-key (ESP32) and role checks on the ingestion and status endpoints.

See docs/designs/edge-cloud-scenarios.md for the scenario IDs.
"""
from __future__ import annotations

import pytest

from app.auth.security import hash_secret
from app.models.common import Role
from app.models.truck import Truck
from app.models.user import User
from beanie import PydanticObjectId
from tests.conftest import auth_headers, fusion_alert_body, status_body


async def test_truck_a_key_on_truck_b_route_is_401_for_alerts(api_client, rig_a, rig_b):
    resp = await api_client.post(
        "/api/alerts", json=fusion_alert_body(rig_b.route_id), headers=rig_a.headers
    )
    assert resp.status_code == 401


async def test_truck_a_key_on_truck_b_route_is_401_for_status(api_client, rig_a, rig_b):
    resp = await api_client.post(
        f"/api/routes/{rig_b.route_id}/status", json=status_body(), headers=rig_a.headers
    )
    assert resp.status_code == 401


async def test_truck_a_key_cannot_resolve_truck_b_alert(api_client, rig_a, rig_b):
    alert = (
        await api_client.post(
            "/api/alerts", json=fusion_alert_body(rig_b.route_id), headers=rig_b.headers
        )
    ).json()
    resp = await api_client.put(
        f"/api/alerts/{alert['id_alert']}",
        json={"resolved_at": "2030-01-01T09:00:00Z"},
        headers=rig_a.headers,
    )
    assert resp.status_code == 401


async def test_rotated_key_old_key_is_401_new_key_201(api_client, root_admin, rig_a):
    rotate = await api_client.post(
        f"/api/trucks/{rig_a.truck_id}/rotate-key", headers=auth_headers(root_admin)
    )
    new_key = rotate.json()["device_api_key"]

    old = await api_client.post(
        f"/api/routes/{rig_a.route_id}/status", json=status_body(), headers=rig_a.headers
    )
    new = await api_client.post(
        f"/api/routes/{rig_a.route_id}/status",
        json=status_body(),
        headers={"X-Device-Api-Key": new_key},
    )
    assert (old.status_code, new.status_code) == (401, 201)


async def test_key_for_truck_without_a_key_hash_is_401(api_client, rig_a):
    truck = await Truck.get(PydanticObjectId(rig_a.truck_id))
    truck.device_api_key_hash = None
    await truck.save()
    resp = await api_client.post(
        f"/api/routes/{rig_a.route_id}/status", json=status_body(), headers=rig_a.headers
    )
    assert resp.status_code == 401


@pytest.mark.parametrize("header", [{}, {"X-Device-Api-Key": ""}], ids=["absent", "empty"])
async def test_missing_or_empty_key_is_401(api_client, rig_a, header):
    resp = await api_client.post(
        f"/api/routes/{rig_a.route_id}/status", json=status_body(), headers=header
    )
    assert resp.status_code == 401


@pytest.mark.xfail(strict=True, reason="BE-16: authenticated user with the wrong role gets 401")
async def test_admin_jwt_posting_alert_is_403_not_401(api_client, admin_user, rig_a):
    resp = await api_client.post(
        "/api/alerts", json=fusion_alert_body(rig_a.route_id), headers=auth_headers(admin_user)
    )
    assert resp.status_code == 403


@pytest.mark.xfail(strict=True, reason="BE-16: authenticated user with the wrong role gets 401")
async def test_truck_driver_jwt_posting_status_is_403_not_401(
    api_client, truck_driver_user, rig_a
):
    resp = await api_client.post(
        f"/api/routes/{rig_a.route_id}/status",
        json=status_body(),
        headers=auth_headers(truck_driver_user),
    )
    assert resp.status_code == 403


async def test_guardian_jwt_can_post_alert_and_status(api_client, guardian, rig_a):
    """Pins the current RBAC (manual testing path). BE-15: the docs' RBAC table said guardians
    were read-only here; the docs are what get fixed, not this."""
    alert = await api_client.post(
        "/api/alerts", json=fusion_alert_body(rig_a.route_id), headers=auth_headers(guardian)
    )
    status = await api_client.post(
        f"/api/routes/{rig_a.route_id}/status", json=status_body(), headers=auth_headers(guardian)
    )
    assert (alert.status_code, status.status_code) == (201, 201)


async def _seed_status(api_client, rig) -> None:
    resp = await api_client.post(
        f"/api/routes/{rig.route_id}/status", json=status_body(), headers=rig.headers
    )
    assert resp.status_code == 201


@pytest.mark.xfail(strict=True, reason="BE-01: GET route status needs no auth (leaks location)")
async def test_get_route_status_without_token_is_401(api_client, rig_a):
    await _seed_status(api_client, rig_a)
    resp = await api_client.get(f"/api/routes/{rig_a.route_id}/status")
    assert resp.status_code == 401


@pytest.mark.xfail(strict=True, reason="BE-01: GET route status needs no auth (leaks location)")
async def test_get_route_status_with_device_key_only_is_401(api_client, rig_a):
    await _seed_status(api_client, rig_a)
    resp = await api_client.get(f"/api/routes/{rig_a.route_id}/status", headers=rig_a.headers)
    assert resp.status_code == 401


@pytest.mark.parametrize("role", list(Role), ids=lambda r: r.value)
async def test_every_role_can_read_route_status(api_client, rig_a, role):
    await _seed_status(api_client, rig_a)
    user = User(
        email=f"{role.value}@example.com",
        password_hash=hash_secret("x" * 64),
        role=role,
        first_name="Any",
        last_name="Role",
        phone_number="+1-555-0009",
    )
    await user.insert()
    resp = await api_client.get(f"/api/routes/{rig_a.route_id}/status", headers=auth_headers(user))
    assert resp.status_code == 200
