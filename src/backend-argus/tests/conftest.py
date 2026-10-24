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

from app.auth.dependencies import invalidate_device_cache
from app.auth.security import create_access_token, hash_secret, key_fingerprint
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


@pytest.fixture(autouse=True)
def _fresh_device_cache():
    """The device-auth cache is process-wide; each test starts with it empty."""
    invalidate_device_cache()
    yield
    invalidate_device_cache()


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
from datetime import date, datetime, timedelta, timezone  # noqa: E402

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


async def make_rig(
    plate: str,
    route_status: RouteStatus = RouteStatus.IN_PROGRESS,
    *,
    fingerprint: bool = True,
    departed_ago: timedelta = timedelta(hours=1),
) -> Rig:
    """A truck with a device key, a driver and one route. A route that has started (in progress
    or finished) departed `departed_ago` before now, and a finished one arrived a minute ago:
    device events are matched to routes by these times (C2.2 rule 4)."""
    key = f"device-key-{plate}"
    truck = Truck(
        plate_number=plate,
        brand="Kenworth",
        model="T680",
        company_number=f"UNIT-{plate}",
        operative_status=TruckStatus.ACTIVE,
        device_api_key_hash=hash_secret(key),
        device_api_key_fingerprint=key_fingerprint(key) if fingerprint else None,
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
    now = datetime.now(timezone.utc)
    started = route_status != RouteStatus.SCHEDULED
    finished = route_status in (RouteStatus.COMPLETED, RouteStatus.CANCELLED)
    route = Route(
        id_driver=str(driver.id),
        id_truck=str(truck.id),
        origin_name="GDL",
        destination_name="CDMX",
        destination_coordinates=GeoJSONPoint(coordinates=(-99.13, 19.43)),
        estimated_departure=datetime(2030, 1, 1, 8, tzinfo=timezone.utc),
        actual_departure=now - departed_ago if started else None,
        actual_arrival=now - timedelta(minutes=1) if finished else None,
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


# --- Report endpoint helpers (POST /api/devices/me/report, C2) ---------------------------------

REPORT_URL = "/api/devices/me/report"


def report_point(**overrides) -> dict:
    """One GPS point as the ESP32 sends it."""
    point = {
        "age_s": 1.0,
        "lat": 20.6597,
        "lon": -103.3496,
        "speed": 62.5,
        "vigilance": "low",
        "last_known": False,
        "location_age_s": 0,
    }
    point.update(overrides)
    return point


def report_event(client_event_id: str = "evt-1", **overrides) -> dict:
    """A live critical alert event as the ESP32 composes it from a Pi record."""
    event = {
        "client_event_id": client_event_id,
        "episode": "a1b2c3d4e5f6",
        "kind": "alert",
        "severity": "critical",
        "alert_type": "eyes_closed",
        "age_s": 1.6,
        "delivery": "live",
        "queued_seconds": 0,
        "position": {"lat": 20.6597, "lon": -103.3496, "last_known": False, "location_age_s": 0},
        "speed": 62.5,
        "ai_metadata": {"model": "parts-rules-v1", "scores": {"not_drowsy": 0.05, "drowsy": 0.95}},
    }
    event.update(overrides)
    return event


def ok_event(client_event_id: str = "ok-1", **overrides) -> dict:
    """A driver-OK event (the episode ended): no severity, no alert_type."""
    event = report_event(client_event_id, kind="ok")
    del event["severity"], event["alert_type"]
    event.update(overrides)
    return event


async def post_report(api_client, rig: Rig, points=None, event=None):
    body: dict = {"points": points if points is not None else []}
    if event is not None:
        body["event"] = event
    return await api_client.post(REPORT_URL, json=body, headers=rig.headers)
