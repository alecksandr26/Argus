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


# --- Edge-ingestion fixtures (device-key writes from the ESP32) --------------------------------
# A "rig" is one truck as the ESP32 sees it: its own driver, one route, and a device API key.
# Two of them make cross-truck checks (key A on truck B's route) one-liners.

from dataclasses import dataclass  # noqa: E402
from datetime import date, datetime, timezone  # noqa: E402

from app.geo import GeoJSONPoint  # noqa: E402
from app.models.common import RouteStatus, TruckStatus  # noqa: E402
from app.models.driver import Driver  # noqa: E402
from app.models.route import Route  # noqa: E402


@dataclass
class Rig:
    truck_id: str
    route_id: str
    key: str

    @property
    def headers(self) -> dict:
        return {"X-Device-Api-Key": self.key}


async def make_rig(plate: str, route_status: RouteStatus = RouteStatus.IN_PROGRESS) -> Rig:
    key = f"device-key-{plate}"
    truck = Truck(
        plate_number=plate,
        brand="Kenworth",
        model="T680",
        company_number=f"UNIT-{plate}",
        operative_status=TruckStatus.ACTIVE,
        device_api_key_hash=hash_secret(key),
    )
    await truck.insert()
    driver = Driver(
        first_name="Edge",
        last_name=plate,
        license_number=f"LIC-{plate}",
        license_expiration=date(2030, 1, 1),
        phone_number="5550000000",
        emergency_contact_name="Contact",
        emergency_contact_phone="5550000001",
        blood_type="O+",
    )
    await driver.insert()
    route = Route(
        id_driver=str(driver.id),
        id_truck=str(truck.id),
        origin_name="GDL",
        destination_name="CDMX",
        destination_coordinates=GeoJSONPoint(coordinates=(-99.13, 19.43)),
        estimated_departure=datetime(2030, 1, 1, 8, tzinfo=timezone.utc),
        operative_status=route_status,
    )
    await route.insert()
    return Rig(truck_id=str(truck.id), route_id=str(route.id), key=key)


@pytest_asyncio.fixture
async def rig_a(mongo_client) -> Rig:
    return await make_rig("RIG-A")


@pytest_asyncio.fixture
async def rig_b(mongo_client) -> Rig:
    return await make_rig("RIG-B")


def fusion_alert_body(route_id: str, **overrides) -> dict:
    """What the ESP32 posts for a fused drowsy+grip alert (see backend_api.cpp)."""
    body = {
        "id_route": route_id,
        "alert_type": "Drowsy",
        "severity_level": "critical",
        "source": "fusion",
        "ai_metadata": {"scores": {"not_drowsy": 0.12, "drowsy": 0.88}},
        "grip_status": "bad",
        "coordinates": {"lat": 20.6582, "lon": -103.3761},
        "speed_at_event": 0.0,
    }
    body.update(overrides)
    return body


def status_body(**overrides) -> dict:
    """What the ESP32 posts for a route_status heartbeat."""
    body = {
        "current_coordinates": {"lat": 20.6582, "lon": -103.3761},
        "current_speed": 0.0,
        "odometer": 0.0,
        "vigilance": "low",
    }
    body.update(overrides)
    return body
