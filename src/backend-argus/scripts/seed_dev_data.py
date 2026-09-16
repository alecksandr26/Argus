"""Loads a small set of demo data into Mongo, shaped like `ui-argus/src/data/fixtures.ts`
(same kind of plates/names/routes) so a reviewer sees consistent demo data whether looking at
the still-mock frontend or one wired up to a real backend. Not a 1:1 port of every fixture row —
a representative handful of each collection, enough to exercise every endpoint including
`GET /api/routes/active`.

Run with: `python -m scripts.seed_dev_data` (from `src/backend-argus/`, after `pip install -e .`
or with `PYTHONPATH=.` set) against whatever `MONGO_URI`/`MONGO_DB` the environment points at.
Safe to re-run — it clears the seeded collections first rather than accumulating duplicates.
"""
from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone

from app.auth.security import generate_device_api_key, hash_secret
from app.database import init_db
from app.geo import to_geojson, Coordinates
from app.models import DOCUMENT_MODELS
from app.models.alert import Alert, AlertAiMetadata
from app.models.common import (
    AlertSeverity,
    DriverStatus,
    Role,
    RouteStatus,
    TruckStatus,
    Vigilance,
)
from app.models.driver import Driver
from app.models.route import Route
from app.models.status_route import StatusRoute
from app.models.truck import Truck
from app.models.user import User


def _password_hash(raw: str) -> str:
    """Every password field on the API now carries a SHA-256 hex digest of the real password
    (computed client-side in the browser, see `app/schemas/common.py`'s `Sha256HexDigest`
    docstring), not the raw password itself. This script writes `User.password_hash` directly
    rather than going through that API, so it has to reproduce the same digest-then-bcrypt
    pipeline by hand for a subsequent real login with `raw` to actually succeed."""
    return hash_secret(hashlib.sha256(raw.encode("utf-8")).hexdigest())


async def seed() -> None:
    await init_db()

    for model in DOCUMENT_MODELS:
        await model.get_motor_collection().delete_many({})

    now = datetime.now(timezone.utc)

    admin = User(
        email="admin@argus.dev",
        password_hash=_password_hash("changeme123"),
        role=Role.ROOT_ADMIN,
        first_name="Ana",
        last_name="Torres",
        phone_number="+52-555-0100",
    )
    guardian = User(
        email="guardian@argus.dev",
        password_hash=_password_hash("changeme123"),
        role=Role.GUARDIAN,
        first_name="Luis",
        last_name="Mendoza",
        phone_number="+52-555-0101",
    )
    operator = User(
        email="operator@argus.dev",
        password_hash=_password_hash("changeme123"),
        role=Role.ADMIN,
        first_name="Sofia",
        last_name="Reyes",
        phone_number="+52-555-0102",
    )
    await admin.insert()
    await guardian.insert()
    await operator.insert()

    truck = Truck(
        plate_number="ARG-4471",
        brand="Kenworth",
        model="T680",
        company_number="TR-014",
        raspberry_pi_mac="B8:27:EB:11:22:33",
        esp32_id="ESP32-014",
        operative_status=TruckStatus.ACTIVE,
    )
    device_key = generate_device_api_key()
    truck.device_api_key_hash = hash_secret(device_key)
    await truck.insert()

    driver = Driver(
        first_name="Jorge",
        last_name="Ramirez",
        license_number="LIC-88213",
        license_expiration=(now + timedelta(days=365)).date(),
        phone_number="+52-555-0202",
        emergency_contact_name="Marta Ramirez",
        emergency_contact_phone="+52-555-0303",
        blood_type="O+",
        operative_status=DriverStatus.ON_ROUTE,
    )
    await driver.insert()

    route = Route(
        id_driver=str(driver.id),
        id_truck=str(truck.id),
        origin_name="CDMX Terminal",
        destination_name="Guadalajara Distribution Center",
        destination_coordinates=to_geojson(Coordinates(lat=20.6597, lon=-103.3496)),
        estimated_departure=now - timedelta(hours=2),
        estimated_arrival=now + timedelta(hours=4),
        actual_departure=now - timedelta(hours=2),
        operative_status=RouteStatus.IN_PROGRESS,
    )
    await route.insert()

    status = StatusRoute(
        id_route=str(route.id),
        current_coordinates=to_geojson(Coordinates(lat=19.6, lon=-99.8)),
        current_speed=87.5,
        odometer=152340.2,
        vigilance=Vigilance.NORMAL,
    )
    await status.insert()

    alert = Alert(
        id_route=str(route.id),
        alert_type="drowsiness",
        severity_level=AlertSeverity.MEDIUM,
        ai_metadata=AlertAiMetadata(scores={"not_drowsy": 0.21, "drowsy": 0.79}),
        coordinates=to_geojson(Coordinates(lat=19.9, lon=-100.1)),
        speed_at_event=82.0,
    )
    await alert.insert()

    print("Seeded dev data:")
    print("  root_admin login: admin@argus.dev / changeme123")
    print("  admin login:      operator@argus.dev / changeme123")
    print("  guardian login:   guardian@argus.dev / changeme123")
    print(f"  truck {truck.plate_number} device API key (save this, shown once): {device_key}")


if __name__ == "__main__":
    asyncio.run(seed())
