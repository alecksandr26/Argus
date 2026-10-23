"""Fixtures wiring the real cv-argus Pi side and the real backend-argus app together.

Imports both modules straight from their source trees, no install needed:
- backend-argus is a plain `app` package, so its directory goes on `sys.path`;
- cv-argus's `src/` only becomes `cv_argus` via setup.py's `package_dir`, so it's registered
  through importlib instead (same trick as `src/tests/smoke_cv_argus.py`).

Only cv-argus's light packages (`alerts`/`buffer`/`sender`/`orchestrator`) are touched, so no
TensorFlow/MediaPipe is needed (numpy only). The backend runs in-process on
mongomock-motor, like its own hermetic test tier.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_SRC / "backend-argus"))

_CV_ARGUS_SRC = _SRC / "cv-argus" / "src"
if "cv_argus" not in sys.modules:
    _spec = importlib.util.spec_from_file_location(
        "cv_argus", _CV_ARGUS_SRC / "__init__.py", submodule_search_locations=[str(_CV_ARGUS_SRC)]
    )
    _module = importlib.util.module_from_spec(_spec)
    sys.modules["cv_argus"] = _module
    _spec.loader.exec_module(_module)

# The rule engine's to_detection() imports cv-argus's `model` package, whose __init__ imports the
# model downloader, which imports gdown. The chain never downloads a model, so an empty stand-in
# keeps that import working where gdown isn't installed (it isn't in src/tests/requirements.txt).
try:
    import gdown  # noqa: F401
except ImportError:
    sys.modules["gdown"] = types.ModuleType("gdown")

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from mongomock_motor import AsyncMongoMockClient  # noqa: E402

from app.auth.security import create_access_token, hash_secret  # noqa: E402
from app.database import init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.common import Role  # noqa: E402
from app.models.user import User  # noqa: E402
from cv_argus.buffer import open_buffer  # noqa: E402
from cv_argus.sender import FakeListener, SenderServer  # noqa: E402


@pytest_asyncio.fixture
async def backend():
    """The real backend app over an in-memory Mongo, plus a root_admin token for setup calls."""
    await init_db(client=AsyncMongoMockClient())
    admin = User(
        email="root@example.com",
        password_hash=hash_secret("x" * 64),
        role=Role.ROOT_ADMIN,
        first_name="Root",
        last_name="Admin",
        phone_number="+00",
    )
    await admin.insert()
    token = create_access_token(str(admin.id), extra_claims={"role": admin.role.value})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://backend") as client:
        client.headers["Authorization"] = f"Bearer {token}"
        yield client


@pytest_asyncio.fixture
async def device_http(backend):
    """What the ESP32 talks to the backend with: same app and database, but no user JWT, so
    only its device key can authorize it (a real device never holds a user token)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://backend") as client:
        yield client


# The route departed this long ago. Events are routed by time (C2.2 rule 4: occurred_at =
# received_at - age_s must fall inside actual_departure..actual_arrival), and the scenarios run a
# fake clock far ahead of the real one, so their events can be minutes "old" when they arrive.
ROUTE_DEPARTED_AGO = timedelta(hours=1)


@pytest_asyncio.fixture
async def truck_route(backend):
    """Provisions a truck + driver + in-progress route the way an operator would (departed an
    hour ago, RouteCreate.actual_departure [sync 1]), and mints the truck's device key the way the
    ESP32 gets provisioned (POST /api/trucks/{id}/rotate-key)."""
    truck = (
        await backend.post(
            "/api/trucks",
            json={
                "plate_number": "EDGE-001",
                "brand": "Kenworth",
                "model": "T680",
                "company_number": "UNIT-EDGE-001",
                "operative_status": "active",
            },
        )
    ).json()
    driver = (
        await backend.post(
            "/api/drivers",
            json={
                "first_name": "Edge",
                "last_name": "Driver",
                "license_number": "LIC-EDGE",
                "license_expiration": "2030-01-01",
                "phone_number": "5550000000",
                "emergency_contact_name": "Contact",
                "emergency_contact_phone": "5550000001",
                "blood_type": "O+",
            },
        )
    ).json()
    route = (
        await backend.post(
            "/api/routes",
            json={
                "id_driver": driver["id_driver"],
                "id_truck": truck["id_truck"],
                "origin_name": "CDMX",
                "destination_name": "GDL",
                "destination_coordinates": {"lat": 20.67, "lon": -103.35},
                "estimated_departure": "2030-01-01T08:00:00Z",
                "actual_departure": (datetime.now(timezone.utc) - ROUTE_DEPARTED_AGO).isoformat(),
                "operative_status": "in_progress",
            },
        )
    ).json()
    key = (await backend.post(f"/api/trucks/{truck['id_truck']}/rotate-key")).json()[
        "device_api_key"
    ]
    return route["id_route"], key


@pytest.fixture
def pi(tmp_path):
    """cv-argus's real Buffer + SenderServer, listening on a FakeListener."""
    buffer = open_buffer(tmp_path / "buffer.sqlite3")
    listener = FakeListener()
    server = SenderServer(buffer, listener, accept_timeout=0.1)
    server.start()
    yield buffer, listener
    server.stop()
    server.join(timeout=2.0)
    buffer.close()


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    return FakeClock()
