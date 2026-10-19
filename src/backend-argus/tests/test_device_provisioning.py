"""Provisioning a truck's edge device from the dashboard: issuing, rotating and revoking its
device key, and the key-status fields `TruckOut` exposes instead of the key itself."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from beanie import PydanticObjectId

from app.models.truck import Truck
from tests.conftest import auth_headers, status_body


async def _rotate(api_client, user, truck_id: str):
    return await api_client.post(f"/api/trucks/{truck_id}/rotate-key", headers=auth_headers(user))


async def test_new_truck_has_no_device_key(api_client, root_admin, sample_truck):
    body = (
        await api_client.get(f"/api/trucks/{sample_truck.id}", headers=auth_headers(root_admin))
    ).json()
    assert body["has_device_key"] is False
    assert body["device_key_issued_at"] is None
    assert body["device_last_seen_at"] is None


async def test_rotate_sets_status_fields_but_never_exposes_the_key(
    api_client, root_admin, sample_truck
):
    assert (await _rotate(api_client, root_admin, str(sample_truck.id))).status_code == 200
    body = (
        await api_client.get(f"/api/trucks/{sample_truck.id}", headers=auth_headers(root_admin))
    ).json()
    assert body["has_device_key"] is True
    assert body["device_key_issued_at"] is not None
    assert body["device_last_seen_at"] is None
    assert not {"device_api_key", "device_api_key_hash", "device_api_key_fingerprint"} & body.keys()


async def test_admin_can_issue_a_device_key(api_client, admin_user, sample_truck):
    resp = await _rotate(api_client, admin_user, str(sample_truck.id))
    assert resp.status_code == 200
    assert resp.json()["device_api_key"]


async def test_other_roles_cannot_issue_or_revoke(
    api_client, guardian, truck_driver_user, sample_truck
):
    for user in (guardian, truck_driver_user):
        assert (await _rotate(api_client, user, str(sample_truck.id))).status_code == 403
        revoke = await api_client.delete(
            f"/api/trucks/{sample_truck.id}/device-key", headers=auth_headers(user)
        )
        assert revoke.status_code == 403


async def test_issued_key_identifies_the_truck(api_client, root_admin, sample_truck):
    key = (await _rotate(api_client, root_admin, str(sample_truck.id))).json()["device_api_key"]
    # No route yet: the key is accepted (404 "no active route"), not rejected (401).
    resp = await api_client.get(
        "/api/devices/me/active-route", headers={"X-Device-Api-Key": key}
    )
    assert resp.status_code == 404


async def test_revoke_makes_the_key_401(api_client, root_admin, rig_a):
    revoke = await api_client.delete(
        f"/api/trucks/{rig_a.truck_id}/device-key", headers=auth_headers(root_admin)
    )
    assert revoke.status_code == 200
    assert revoke.json()["has_device_key"] is False
    assert revoke.json()["device_key_issued_at"] is None

    write = await api_client.post(
        f"/api/routes/{rig_a.route_id}/status", json=status_body(), headers=rig_a.headers
    )
    lookup = await api_client.get("/api/devices/me/active-route", headers=rig_a.headers)
    assert (write.status_code, lookup.status_code) == (401, 401)


async def test_revoke_unknown_truck_is_404(api_client, root_admin):
    resp = await api_client.delete(
        f"/api/trucks/{PydanticObjectId()}/device-key", headers=auth_headers(root_admin)
    )
    assert resp.status_code == 404


async def test_device_request_records_last_seen(api_client, root_admin, rig_a):
    await api_client.post(
        f"/api/routes/{rig_a.route_id}/status", json=status_body(), headers=rig_a.headers
    )
    body = (
        await api_client.get(f"/api/trucks/{rig_a.truck_id}", headers=auth_headers(root_admin))
    ).json()
    assert body["device_last_seen_at"] is not None


async def test_last_seen_is_throttled(api_client, rig_a):
    recent = datetime.now(timezone.utc) - timedelta(seconds=5)
    truck = await Truck.get(PydanticObjectId(rig_a.truck_id))
    truck.device_last_seen_at = recent
    await truck.save()

    await api_client.get("/api/devices/me/active-route", headers=rig_a.headers)
    truck = await Truck.get(PydanticObjectId(rig_a.truck_id))
    assert truck.device_last_seen_at.replace(tzinfo=timezone.utc) == recent.replace(
        microsecond=recent.microsecond // 1000 * 1000
    )


async def test_stale_last_seen_is_refreshed(api_client, rig_a):
    stale = datetime.now(timezone.utc) - timedelta(minutes=10)
    truck = await Truck.get(PydanticObjectId(rig_a.truck_id))
    truck.device_last_seen_at = stale
    await truck.save()

    await api_client.get("/api/devices/me/active-route", headers=rig_a.headers)
    truck = await Truck.get(PydanticObjectId(rig_a.truck_id))
    assert truck.device_last_seen_at.replace(tzinfo=timezone.utc) > stale + timedelta(minutes=9)


async def test_rotate_resets_last_seen(api_client, root_admin, rig_a):
    await api_client.get("/api/devices/me/active-route", headers=rig_a.headers)
    await _rotate(api_client, root_admin, rig_a.truck_id)
    truck = await Truck.get(PydanticObjectId(rig_a.truck_id))
    assert truck.device_last_seen_at is None
