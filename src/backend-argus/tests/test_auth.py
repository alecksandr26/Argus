import pytest


@pytest.mark.asyncio
async def test_login_success(api_client, root_admin):
    resp = await api_client.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": "password123"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["user"]["role"] == "root_admin"


@pytest.mark.asyncio
async def test_login_wrong_password(api_client, root_admin):
    resp = await api_client.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": "wrong"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_unknown_email(api_client, mongo_client):
    resp = await api_client.post(
        "/api/auth/login", json={"email": "nobody@example.com", "password": "password123"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_protected_route_requires_token(api_client, mongo_client):
    resp = await api_client.get("/api/trucks")
    assert resp.status_code == 401
