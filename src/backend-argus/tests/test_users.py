"""No prior coverage existed for `POST /api/users` at all. This is a minimal test proving the
new SHA-256-digest password contract round-trips correctly end to end: a root_admin creates a
user with a digest password, and that same raw password (re-digested, exactly like a real
browser login would) then logs in successfully."""
import pytest

from tests.conftest import auth_headers, sha256_hex


@pytest.mark.asyncio
async def test_create_user_then_login_with_same_password(api_client, root_admin):
    create_resp = await api_client.post(
        "/api/users",
        json={
            "email": "new-guardian@example.com",
            "password": sha256_hex("a-fresh-password"),
            "role": "guardian",
            "first_name": "New",
            "last_name": "Guardian",
            "phone_number": "+1-555-4321",
        },
        headers=auth_headers(root_admin),
    )
    assert create_resp.status_code == 201
    assert "password" not in create_resp.json()
    assert "password_hash" not in create_resp.json()

    login_resp = await api_client.post(
        "/api/auth/login",
        json={"email": "new-guardian@example.com", "password": sha256_hex("a-fresh-password")},
    )
    assert login_resp.status_code == 200
    assert login_resp.json()["user"]["role"] == "guardian"


@pytest.mark.asyncio
async def test_create_user_rejects_non_digest_password(api_client, root_admin):
    resp = await api_client.post(
        "/api/users",
        json={
            "email": "bad-password@example.com",
            "password": "raw-plaintext-password",
            "role": "guardian",
            "first_name": "Bad",
            "last_name": "Password",
            "phone_number": "+1-555-0000",
        },
        headers=auth_headers(root_admin),
    )
    assert resp.status_code == 422


# --- `admin`'s scoped-to-guardian-only user management (see app/routers/users.py) ---


@pytest.mark.asyncio
async def test_admin_can_create_and_manage_a_guardian(api_client, admin_user):
    create_resp = await api_client.post(
        "/api/users",
        json={
            "email": "admin-made-guardian@example.com",
            "password": sha256_hex("password123"),
            "role": "guardian",
            "first_name": "Made",
            "last_name": "ByAdmin",
            "phone_number": "+1-555-7777",
        },
        headers=auth_headers(admin_user),
    )
    assert create_resp.status_code == 201
    guardian_id = create_resp.json()["id_user"]

    get_resp = await api_client.get(f"/api/users/{guardian_id}", headers=auth_headers(admin_user))
    assert get_resp.status_code == 200

    update_resp = await api_client.put(
        f"/api/users/{guardian_id}",
        json={"phone_number": "+1-555-8888"},
        headers=auth_headers(admin_user),
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["phone_number"] == "+1-555-8888"

    list_resp = await api_client.get("/api/users", headers=auth_headers(admin_user))
    assert list_resp.status_code == 200
    assert all(u["role"] == "guardian" for u in list_resp.json())

    delete_resp = await api_client.delete(
        f"/api/users/{guardian_id}", headers=auth_headers(admin_user)
    )
    assert delete_resp.status_code == 200


@pytest.mark.asyncio
async def test_admin_cannot_create_non_guardian_user(api_client, admin_user):
    for role in ("root_admin", "admin"):
        resp = await api_client.post(
            "/api/users",
            json={
                "email": f"attempt-{role}@example.com",
                "password": sha256_hex("password123"),
                "role": role,
                "first_name": "No",
                "last_name": "Way",
                "phone_number": "+1-555-9999",
            },
            headers=auth_headers(admin_user),
        )
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_cannot_see_or_touch_root_admin_or_other_admins(
    api_client, admin_user, root_admin
):
    root_admin_id = str(root_admin.id)

    get_resp = await api_client.get(
        f"/api/users/{root_admin_id}", headers=auth_headers(admin_user)
    )
    assert get_resp.status_code == 404

    update_resp = await api_client.put(
        f"/api/users/{root_admin_id}",
        json={"phone_number": "+1-555-0000"},
        headers=auth_headers(admin_user),
    )
    assert update_resp.status_code == 404

    delete_resp = await api_client.delete(
        f"/api/users/{root_admin_id}", headers=auth_headers(admin_user)
    )
    assert delete_resp.status_code == 404

    list_resp = await api_client.get("/api/users", headers=auth_headers(admin_user))
    assert list_resp.status_code == 200
    assert all(u["id_user"] != root_admin_id for u in list_resp.json())


@pytest.mark.asyncio
async def test_admin_cannot_promote_a_guardian_via_update(api_client, admin_user):
    create_resp = await api_client.post(
        "/api/users",
        json={
            "email": "guardian-to-promote@example.com",
            "password": sha256_hex("password123"),
            "role": "guardian",
            "first_name": "Try",
            "last_name": "Promote",
            "phone_number": "+1-555-1212",
        },
        headers=auth_headers(admin_user),
    )
    guardian_id = create_resp.json()["id_user"]

    resp = await api_client.put(
        f"/api/users/{guardian_id}",
        json={"role": "root_admin"},
        headers=auth_headers(admin_user),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_root_admin_unrestricted_over_admin_accounts(api_client, root_admin, admin_user):
    admin_id = str(admin_user.id)
    get_resp = await api_client.get(f"/api/users/{admin_id}", headers=auth_headers(root_admin))
    assert get_resp.status_code == 200
    assert get_resp.json()["role"] == "admin"

    update_resp = await api_client.put(
        f"/api/users/{admin_id}",
        json={"is_active": False},
        headers=auth_headers(root_admin),
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["is_active"] is False
