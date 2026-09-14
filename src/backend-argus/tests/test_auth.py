import pytest

from tests.conftest import sha256_hex


@pytest.mark.asyncio
async def test_login_success(api_client, root_admin):
    resp = await api_client.post(
        "/api/auth/login",
        json={"email": "admin@example.com", "password": sha256_hex("password123")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["user"]["role"] == "root_admin"


@pytest.mark.asyncio
async def test_login_wrong_password(api_client, root_admin):
    resp = await api_client.post(
        "/api/auth/login",
        json={"email": "admin@example.com", "password": sha256_hex("wrong")},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_unknown_email(api_client, mongo_client):
    resp = await api_client.post(
        "/api/auth/login",
        json={"email": "nobody@example.com", "password": sha256_hex("password123")},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_rejects_non_digest_password(api_client, root_admin):
    """The `password` field must be a 64-char lowercase hex SHA-256 digest (see
    `app/schemas/common.py`'s `Sha256HexDigest`) — a raw password or any other malformed value
    should fail schema validation (422) before it ever reaches `verify_secret`, not be treated
    as simply "wrong" (401)."""
    resp = await api_client.post(
        "/api/auth/login",
        json={"email": "admin@example.com", "password": "not-a-digest"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_protected_route_requires_token(api_client, mongo_client):
    resp = await api_client.get("/api/trucks")
    assert resp.status_code == 401
