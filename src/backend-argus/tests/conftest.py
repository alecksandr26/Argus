"""Shared fixtures. Default tier is fully hermetic — no real Mongo, no network — backed by
`mongomock-motor`'s in-memory Motor-compatible client. The opt-in `mongo` marker (see
pyproject.toml's addopts) is for the handful of tests that need real `2dsphere` behavior
mongomock can't faithfully emulate; those tests build their own real-Mongo fixture inline
via testcontainers, gated the same way src/cv-argus's `docker` marker is (see that module's
CLAUDE.md's "Tests" section for the convention this follows).
"""
from __future__ import annotations

import hashlib

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient

from app.auth.security import create_access_token, hash_secret
from app.database import init_db
from app.main import app
from app.models.common import Role
from app.models.truck import Truck
from app.models.user import User


def sha256_hex(raw: str) -> str:
    """Every password field on the API now carries the SHA-256 hex digest of the real password
    (see `app/schemas/common.py`'s `Sha256HexDigest` docstring), not the raw password — tests
    that log in or create users need to send this instead of a literal password string."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@pytest_asyncio.fixture
async def mongo_client():
    """A fresh in-memory Mongo per test — re-running init_beanie against a new client each time
    so no state (or unique-index violations) leaks between tests."""
    client = AsyncMongoMockClient()
    await init_db(client=client)
    return client


@pytest_asyncio.fixture
async def api_client(mongo_client):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def root_admin(mongo_client) -> User:
    user = User(
        email="admin@example.com",
        password_hash=hash_secret(sha256_hex("password123")),
        role=Role.ROOT_ADMIN,
        first_name="Root",
        last_name="Admin",
        phone_number="+1-555-0000",
    )
    await user.insert()
    return user


@pytest_asyncio.fixture
async def guardian(mongo_client) -> User:
    user = User(
        email="guardian@example.com",
        password_hash=hash_secret(sha256_hex("password123")),
        role=Role.GUARDIAN,
        first_name="Guard",
        last_name="Ian",
        phone_number="+1-555-0001",
    )
    await user.insert()
    return user


@pytest_asyncio.fixture
async def admin_user(mongo_client) -> User:
    user = User(
        email="admin-role@example.com",
        password_hash=hash_secret(sha256_hex("password123")),
        role=Role.ADMIN,
        first_name="Fleet",
        last_name="Operator",
        phone_number="+1-555-0003",
    )
    await user.insert()
    return user


@pytest_asyncio.fixture
async def truck_driver_user(mongo_client) -> User:
    user = User(
        email="driver@example.com",
        password_hash=hash_secret(sha256_hex("password123")),
        role=Role.TRUCK_DRIVER,
        first_name="Truck",
        last_name="Driver",
        phone_number="+1-555-0002",
    )
    await user.insert()
    return user


def auth_headers(user: User) -> dict:
    token = create_access_token(subject=str(user.id), extra_claims={"role": user.role.value})
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def sample_truck(mongo_client) -> Truck:
    truck = Truck(
        plate_number="TEST-001",
        brand="Volvo",
        model="FH16",
        company_number="TR-001",
    )
    await truck.insert()
    return truck
