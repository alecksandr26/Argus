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
