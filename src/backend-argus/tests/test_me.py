"""`GET`/`PUT /api/auth/me` — the self-service profile endpoint every authenticated role gets,
independent of `/api/users` access. Confirms the digest-password contract round-trips exactly
like `/api/users` does, and that `MeUpdate`'s lack of a `role`/`is_active` field is a real
security boundary, not just an unenforced convention."""
import pytest

from tests.conftest import auth_headers, sha256_hex


@pytest.mark.asyncio
async def test_get_me_returns_own_profile(api_client, guardian):
    resp = await api_client.get("/api/auth/me", headers=auth_headers(guardian))
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == guardian.email
    assert body["role"] == "guardian"
    assert "password" not in body and "password_hash" not in body


@pytest.mark.asyncio
async def test_any_role_can_edit_own_profile_and_login_with_new_password(
    api_client, truck_driver_user
):
    resp = await api_client.put(
        "/api/auth/me",
        json={"phone_number": "+1-555-2468", "password": sha256_hex("a-new-password")},
        headers=auth_headers(truck_driver_user),
    )
    assert resp.status_code == 200
    assert resp.json()["phone_number"] == "+1-555-2468"

    login_resp = await api_client.post(
        "/api/auth/login",
        json={"email": truck_driver_user.email, "password": sha256_hex("a-new-password")},
    )
    assert login_resp.status_code == 200


@pytest.mark.asyncio
async def test_update_me_duplicate_email_conflicts(api_client, guardian, truck_driver_user):
    resp = await api_client.put(
        "/api/auth/me",
        json={"email": guardian.email},
        headers=auth_headers(truck_driver_user),
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_update_me_same_email_is_a_noop_not_a_conflict(api_client, guardian):
    resp = await api_client.put(
        "/api/auth/me",
        json={"email": guardian.email, "first_name": "StillGuard"},
        headers=auth_headers(guardian),
    )
    assert resp.status_code == 200
    assert resp.json()["first_name"] == "StillGuard"


@pytest.mark.asyncio
async def test_update_me_cannot_smuggle_a_role_change(api_client, guardian):
    resp = await api_client.put(
        "/api/auth/me",
        json={"role": "root_admin", "is_active": False, "first_name": "StillGuard"},
        headers=auth_headers(guardian),
    )
    # The schema has no `role`/`is_active` fields at all, so FastAPI/Pydantic silently drops
    # them rather than erroring — the request succeeds, but only the recognized field changes.
    assert resp.status_code == 200
    body = resp.json()
    assert body["role"] == "guardian"
    assert body["is_active"] is True
    assert body["first_name"] == "StillGuard"
