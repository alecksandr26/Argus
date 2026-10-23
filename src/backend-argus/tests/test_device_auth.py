"""Device-key (ESP32) and role checks on the ingestion and status endpoints.

See docs/designs/edge-cloud-scenarios.md for the scenario IDs.
"""
from __future__ import annotations

import pytest

import app.auth.dependencies as deps
from app.auth.security import hash_secret, key_fingerprint
from app.models.common import Role
from app.models.truck import Truck
from app.models.user import User
from beanie import PydanticObjectId
from tests.conftest import auth_headers, fusion_alert_body, make_rig, post_report, status_body


# --- Warm-container cache (C2: DEVICE_AUTH_CACHE_TTL) -------------------------------------------


@pytest.fixture
def bcrypt_calls(monkeypatch):
    """Counts bcrypt verifications of device keys."""
    calls: list[str] = []
    real = deps.verify_secret

    def counting(plaintext, hashed):
        calls.append(plaintext)
        return real(plaintext, hashed)

    monkeypatch.setattr(deps, "verify_secret", counting)
    return calls


async def test_second_report_skips_bcrypt(api_client, rig_a, bcrypt_calls):
    for _ in range(3):
        assert (await post_report(api_client, rig_a)).status_code == 200
    assert len(bcrypt_calls) == 1


async def test_cache_also_serves_the_legacy_route_endpoints(api_client, rig_a, bcrypt_calls):
    await post_report(api_client, rig_a)
    resp = await api_client.post(
        f"/api/routes/{rig_a.route_id}/status", json=status_body(), headers=rig_a.headers
    )
    assert resp.status_code == 201
    assert len(bcrypt_calls) == 1


async def test_expired_cache_entry_checks_bcrypt_again(api_client, rig_a, bcrypt_calls):
    await post_report(api_client, rig_a)
    fingerprint = key_fingerprint(rig_a.key)
    truck_id, _ = deps._device_cache[fingerprint]
    deps._device_cache[fingerprint] = (truck_id, 0.0)
    assert (await post_report(api_client, rig_a)).status_code == 200
    assert len(bcrypt_calls) == 2


async def test_rotate_rejects_a_cached_key_at_once(api_client, root_admin, rig_a):
    assert (await post_report(api_client, rig_a)).status_code == 200
    await api_client.post(f"/api/trucks/{rig_a.truck_id}/rotate-key", headers=auth_headers(root_admin))
    assert (await post_report(api_client, rig_a)).status_code == 401


async def test_revoke_rejects_a_cached_key_at_once(api_client, root_admin, rig_a):
    assert (await post_report(api_client, rig_a)).status_code == 200
    await api_client.delete(f"/api/trucks/{rig_a.truck_id}/device-key", headers=auth_headers(root_admin))
    assert (await post_report(api_client, rig_a)).status_code == 401


async def test_key_changed_by_another_container_is_rejected(api_client, rig_a):
    """Another warm container rotated the key: this container's cache entry is still there,
    but the truck's stored fingerprint no longer matches, so the old key fails at once."""
    assert (await post_report(api_client, rig_a)).status_code == 200
    truck = await Truck.get(PydanticObjectId(rig_a.truck_id))
    await truck.set(
        {
            "device_api_key_hash": hash_secret("new-key"),
            "device_api_key_fingerprint": key_fingerprint("new-key"),
        }
    )
    assert key_fingerprint(rig_a.key) in deps._device_cache
    assert (await post_report(api_client, rig_a)).status_code == 401


async def test_legacy_key_without_fingerprint_gets_one(api_client, mongo_client):
    rig = await make_rig("LEGACY-KEY", fingerprint=False)
    assert (await post_report(api_client, rig)).status_code == 200
    truck = await Truck.get(PydanticObjectId(rig.truck_id))
    assert truck.device_api_key_fingerprint == key_fingerprint(rig.key)


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


async def test_admin_jwt_posting_alert_is_403_not_401(api_client, admin_user, rig_a):
    resp = await api_client.post(
        "/api/alerts", json=fusion_alert_body(rig_a.route_id), headers=auth_headers(admin_user)
    )
    assert resp.status_code == 403


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


async def test_get_route_status_without_token_is_401(api_client, rig_a):
    await _seed_status(api_client, rig_a)
    resp = await api_client.get(f"/api/routes/{rig_a.route_id}/status")
    assert resp.status_code == 401


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
