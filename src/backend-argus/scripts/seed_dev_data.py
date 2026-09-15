"""Loads a small fleet's worth of demo data into Mongo: a handful of admin/operator and
guardian accounts, several trucks and drivers, and a few routes/alerts so the live dashboard
has something to show. Not a 1:1 port of any particular fixture set — a representative handful
of each collection, enough to exercise every endpoint including `GET /api/routes/active`.

Two ways this runs, sharing the same dataset defined below so there's one source of truth:

- **Manual, destructive** (`python -m scripts.seed_dev_data`, or `RESET_DEMO_DATA=true`):
  clears every collection first, then inserts the full dataset fresh. Deterministic — always
  the same demo state — but wipes anything else in the database, including data created through
  the UI. Use this when you want a clean, known-good demo dataset and don't care about losing
  whatever was there before.
- **Idempotent, startup-safe** (`SEED_DEMO_DATA=true` env var, wired into `app.main`'s lifespan
  — see `app/config.py`'s `seed_demo_data`): runs the same dataset but only *inserts records
  that don't already exist* (matched by email/plate_number/license_number), and only adds the
  demo routes/status/alerts if the `Route` collection is currently empty. Safe to leave this env
  var on permanently in a dev `docker-compose.yml` — it won't re-wipe or duplicate data on every
  container restart.

Run manually with: `python -m scripts.seed_dev_data` (from `src/backend-argus/`, after
`pip install -e .` or with `PYTHONPATH=.` set) against whatever `MONGO_URI`/`MONGO_DB` the
environment points at.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
from datetime import datetime, timedelta, timezone

from app.auth.security import generate_device_api_key, hash_secret
from app.database import init_db
from app.geo import to_geojson, Coordinates
from app.models import DOCUMENT_MODELS
from app.models.alert import Alert, AlertAiMetadata
from app.models.common import (
    AlertSource,
    DriverStatus,
    Role,
    RouteStatus,
    Severity,
    TruckStatus,
)
from app.models.driver import Driver
from app.models.route import Route
from app.models.status_route import StatusRoute
from app.models.truck import Truck
from app.models.user import User

DEMO_PASSWORD = "changeme123"


def _password_hash(raw: str) -> str:
    """Every password field on the API now carries a SHA-256 hex digest of the real password
    (computed client-side in the browser, see `app/schemas/common.py`'s `Sha256HexDigest`
    docstring), not the raw password itself. This script writes `User.password_hash` directly
    rather than going through that API, so it has to reproduce the same digest-then-bcrypt
    pipeline by hand for a subsequent real login with `raw` to actually succeed."""
    return hash_secret(hashlib.sha256(raw.encode("utf-8")).hexdigest())


# (email, role, first_name, last_name, phone_number) — one root_admin (matches
# `app/auth/bootstrap.py`'s own default so a bare `docker compose up` and this script agree),
# three admin/operators, three guardians.
_USERS: list[tuple[str, Role, str, str, str]] = [
    ("admin@argus.dev", Role.ROOT_ADMIN, "Ana", "Torres", "+52-555-0100"),
    ("operator@argus.dev", Role.ADMIN, "Sofia", "Reyes", "+52-555-0102"),
    ("operator2@argus.dev", Role.ADMIN, "Miguel", "Castillo", "+52-555-0103"),
    ("operator3@argus.dev", Role.ADMIN, "Daniela", "Ortiz", "+52-555-0104"),
    ("guardian@argus.dev", Role.GUARDIAN, "Luis", "Mendoza", "+52-555-0101"),
    ("guardian2@argus.dev", Role.GUARDIAN, "Patricia", "Flores", "+52-555-0105"),
    ("guardian3@argus.dev", Role.GUARDIAN, "Ricardo", "Salinas", "+52-555-0106"),
]

# (plate_number, brand, model, company_number, status) — spans every TruckStatus so the Fleet
# screen shows real variety, not five identical "active" rows.
_TRUCKS: list[tuple[str, str, str, str, TruckStatus]] = [
    ("ARG-4471", "Kenworth", "T680", "TR-014", TruckStatus.ACTIVE),
    ("ARG-2201", "Volvo", "FH16", "TR-015", TruckStatus.ACTIVE),
    ("ARG-3390", "Freightliner", "Cascadia", "TR-016", TruckStatus.ALERT),
    ("ARG-1150", "International", "LT Series", "TR-017", TruckStatus.MAINTENANCE),
    ("ARG-5820", "Mack", "Anthem", "TR-018", TruckStatus.INACTIVE),
]

# (first_name, last_name, license_number, phone, emergency_name, emergency_phone, blood_type,
# status) — index-paired with _TRUCKS above for the demo routes below.
_DRIVERS: list[tuple[str, str, str, str, str, str, str, DriverStatus]] = [
    ("Jorge", "Ramirez", "LIC-88213", "+52-555-0202", "Marta Ramirez", "+52-555-0303", "O+", DriverStatus.ON_ROUTE),
    ("Alejandro", "Vega", "LIC-77102", "+52-555-0204", "Rosa Vega", "+52-555-0305", "A+", DriverStatus.ON_ROUTE),
    ("Fernando", "Cruz", "LIC-66391", "+52-555-0206", "Elena Cruz", "+52-555-0307", "B+", DriverStatus.ON_ROUTE_ALERT),
    ("Gabriel", "Morales", "LIC-55480", "+52-555-0208", "Sara Morales", "+52-555-0309", "O-", DriverStatus.RESTING),
    ("Hector", "Delgado", "LIC-44579", "+52-555-0210", "Lucia Delgado", "+52-555-0311", "AB+", DriverStatus.INACTIVE),
]


async def _seed_users() -> None:
    for email, role, first_name, last_name, phone_number in _USERS:
        if await User.find_one(User.email == email) is not None:
            continue
        await User(
            email=email,
            password_hash=_password_hash(DEMO_PASSWORD),
            role=role,
            first_name=first_name,
            last_name=last_name,
            phone_number=phone_number,
        ).insert()


async def _seed_trucks_and_drivers() -> tuple[list[Truck], list[Driver]]:
    trucks: list[Truck] = []
    for plate_number, brand, model, company_number, op_status in _TRUCKS:
        existing = await Truck.find_one(Truck.plate_number == plate_number)
        if existing is not None:
            trucks.append(existing)
            continue
        truck = Truck(
            plate_number=plate_number,
            brand=brand,
            model=model,
            company_number=company_number,
            raspberry_pi_mac=f"B8:27:EB:{len(trucks):02d}:00:00",
            esp32_id=f"ESP32-{company_number}",
            operative_status=op_status,
        )
        truck.device_api_key_hash = hash_secret(generate_device_api_key())
        await truck.insert()
        trucks.append(truck)

    drivers: list[Driver] = []
    now = datetime.now(timezone.utc)
    for first, last, license_number, phone, emg_name, emg_phone, blood, op_status in _DRIVERS:
        existing = await Driver.find_one(Driver.license_number == license_number)
        if existing is not None:
            drivers.append(existing)
            continue
        driver = Driver(
            first_name=first,
            last_name=last,
            license_number=license_number,
            license_expiration=(now + timedelta(days=365)).date(),
            phone_number=phone,
            emergency_contact_name=emg_name,
            emergency_contact_phone=emg_phone,
            blood_type=blood,
            operative_status=op_status,
        )
        await driver.insert()
        drivers.append(driver)

    return trucks, drivers


async def _seed_routes_and_alerts(trucks: list[Truck], drivers: list[Driver]) -> None:
    """Only called when the `Route` collection is empty (no stable unique key to dedupe routes
    by otherwise) — see `seed()`'s `reset`/idempotent branching below."""
    now = datetime.now(timezone.utc)

    route1 = Route(
        id_driver=str(drivers[0].id),
        id_truck=str(trucks[0].id),
        origin_name="CDMX Terminal",
        destination_name="Guadalajara Distribution Center",
        destination_coordinates=to_geojson(Coordinates(lat=20.6597, lon=-103.3496)),
        estimated_departure=now - timedelta(hours=2),
        estimated_arrival=now + timedelta(hours=4),
        actual_departure=now - timedelta(hours=2),
        operative_status=RouteStatus.IN_PROGRESS,
    )
    await route1.insert()
    await StatusRoute(
        id_route=str(route1.id),
        current_coordinates=to_geojson(Coordinates(lat=19.6, lon=-99.8)),
        current_speed=87.5,
        odometer=152340.2,
        vigilance=Severity.LOW,
    ).insert()

    # A recovered incident on route1: a fused medium alert (Drowsy + good grip, per
    # fusion_contract.py's BASE_MATRIX) that escalated to critical once grip also went bad,
    # then recovered — demonstrates `related_alert_id`/`resolved_at`, which the single-alert
    # routes below don't exercise.
    medium_alert = Alert(
        id_route=str(route1.id),
        alert_type="drowsiness",
        severity_level=Severity.MEDIUM,
        source=AlertSource.FUSION,
        ai_metadata=AlertAiMetadata(scores={"not_drowsy": 0.21, "drowsy": 0.79}),
        grip_status="good",
        coordinates=to_geojson(Coordinates(lat=19.6, lon=-99.8)),
        speed_at_event=85.0,
    )
    await medium_alert.insert()
    critical_alert = Alert(
        id_route=str(route1.id),
        alert_type="drowsiness",
        severity_level=Severity.CRITICAL,
        source=AlertSource.FUSION,
        ai_metadata=AlertAiMetadata(scores={"not_drowsy": 0.08, "drowsy": 0.92}),
        grip_status="bad",
        related_alert_id=str(medium_alert.id),
        coordinates=to_geojson(Coordinates(lat=19.6, lon=-99.8)),
        speed_at_event=83.0,
        resolved_at=now - timedelta(minutes=20),
        reviewed_by_operator=True,
        operator_notes="Driver alert, grip recovered — pulled over briefly then resumed.",
    )
    await critical_alert.insert()

    route2 = Route(
        id_driver=str(drivers[2].id),
        id_truck=str(trucks[2].id),
        origin_name="Monterrey Hub",
        destination_name="Saltillo Cross-dock",
        destination_coordinates=to_geojson(Coordinates(lat=25.4232, lon=-100.9855)),
        estimated_departure=now - timedelta(hours=1),
        estimated_arrival=now + timedelta(hours=1),
        actual_departure=now - timedelta(hours=1),
        operative_status=RouteStatus.IN_PROGRESS_ALERT,
    )
    await route2.insert()
    await StatusRoute(
        id_route=str(route2.id),
        current_coordinates=to_geojson(Coordinates(lat=25.5, lon=-100.9)),
        current_speed=91.0,
        odometer=88210.4,
        vigilance=Severity.CRITICAL,
    ).insert()
    await Alert(
        id_route=str(route2.id),
        alert_type="drowsiness",
        severity_level=Severity.CRITICAL,
        source=AlertSource.FUSION,
        ai_metadata=AlertAiMetadata(scores={"not_drowsy": 0.09, "drowsy": 0.91}),
        grip_status="bad",
        coordinates=to_geojson(Coordinates(lat=25.5, lon=-100.9)),
        speed_at_event=91.0,
    ).insert()
    # A panic-button event on the same route — independent of the drowsiness pipeline entirely
    # (no camera/grip evaluation), demonstrating `AlertSource.PANIC_BUTTON`.
    await Alert(
        id_route=str(route2.id),
        alert_type="Panic button pressed",
        severity_level=Severity.CRITICAL,
        source=AlertSource.PANIC_BUTTON,
        coordinates=to_geojson(Coordinates(lat=25.48, lon=-100.95)),
        speed_at_event=88.0,
    ).insert()

    route3 = Route(
        id_driver=str(drivers[3].id),
        id_truck=str(trucks[1].id),
        origin_name="Puebla Terminal",
        destination_name="Veracruz Port",
        destination_coordinates=to_geojson(Coordinates(lat=19.1738, lon=-96.1342)),
        estimated_departure=now + timedelta(hours=6),
        operative_status=RouteStatus.SCHEDULED,
    )
    await route3.insert()

    route4 = Route(
        id_driver=str(drivers[4].id),
        id_truck=str(trucks[4].id),
        origin_name="Tijuana Yard",
        destination_name="Mexicali Depot",
        destination_coordinates=to_geojson(Coordinates(lat=32.6245, lon=-115.4523)),
        estimated_departure=now - timedelta(days=1, hours=3),
        estimated_arrival=now - timedelta(days=1),
        actual_departure=now - timedelta(days=1, hours=3),
        actual_arrival=now - timedelta(days=1),
        operative_status=RouteStatus.COMPLETED,
    )
    await route4.insert()
    await Alert(
        id_route=str(route4.id),
        alert_type="drowsiness",
        severity_level=Severity.MEDIUM,
        source=AlertSource.FUSION,
        ai_metadata=AlertAiMetadata(scores={"not_drowsy": 0.21, "drowsy": 0.79}),
        grip_status="good",
        coordinates=to_geojson(Coordinates(lat=32.6, lon=-115.4)),
        speed_at_event=82.0,
        reviewed_by_operator=True,
        operator_notes="Checked footage, driver pulled over shortly after — false positive.",
    ).insert()


def _write_credentials_file(path: str) -> None:
    """Writes a plaintext `role  email  password` line per seeded user (see `_USERS` above) to
    `path`, for grabbing test-login credentials without reading source — e.g. from `it-argus`'s
    Playwright tests or a manual QA pass. Not a new secret: every password here is the same
    fixed, already-public `DEMO_PASSWORD` constant this module already prints to stdout and
    documents in README.md. Local/testing use only — never point this at a path a real
    deployment serves or that gets committed."""
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    lines = [
        "# Argus demo credentials (scripts/seed_dev_data.py) — local/testing use only",
        f"# generated {datetime.now(timezone.utc).isoformat()}",
        "",
    ]
    for email, role, _first_name, _last_name, _phone in _USERS:
        lines.append(f"{role.value:<12} {email:<24} {DEMO_PASSWORD}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


async def seed(reset: bool = True, client=None, credentials_file: str | None = None) -> None:
    """`reset=True` (the manual-script default): wipe every collection first, then insert the
    full demo dataset fresh — deterministic, but destructive. `reset=False`: additive/idempotent
    — only inserts users/trucks/drivers that don't already exist (by email/plate_number/
    license_number), and only adds demo routes/status/alerts if `Route` is currently empty. Safe
    to call on every backend startup when `reset=False`.

    `client`: an already-connected Motor client to reuse (e.g. `app.state.mongo_client` from
    `app.main`'s lifespan) instead of opening a second, separate connection — `init_db()` opens
    its own only when `client` is `None`, which is what the standalone
    `python -m scripts.seed_dev_data` entry point below relies on.

    `credentials_file`: when given, also writes every seeded user's role/email/password to this
    path (see `_write_credentials_file`) — opt-in, `None` by default so a plain test run never
    touches the filesystem."""
    await init_db(client=client)

    if reset:
        for model in DOCUMENT_MODELS:
            await model.get_motor_collection().delete_many({})

    await _seed_users()
    trucks, drivers = await _seed_trucks_and_drivers()

    if reset or await Route.find_all().count() == 0:
        await _seed_routes_and_alerts(trucks, drivers)

    print("Seeded demo data:" if reset else "Ensured demo data (idempotent):")
    print(f"  root_admin login: admin@argus.dev / {DEMO_PASSWORD}")
    print(f"  admin logins:     operator@argus.dev, operator2@argus.dev, operator3@argus.dev / {DEMO_PASSWORD}")
    print(f"  guardian logins:  guardian@argus.dev, guardian2@argus.dev, guardian3@argus.dev / {DEMO_PASSWORD}")
    print(f"  trucks seeded:    {', '.join(t.plate_number for t in trucks)}")

    if credentials_file:
        _write_credentials_file(credentials_file)
        print(f"  credentials file: {credentials_file}")


if __name__ == "__main__":
    # `RESET_DEMO_DATA=false python -m scripts.seed_dev_data` runs the idempotent path manually
    # too, if you want to add missing demo records without wiping the database.
    # `SEED_CREDENTIALS_FILE=./seed_credentials.txt python -m scripts.seed_dev_data` additionally
    # writes every seeded user's role/email/password to that file.
    asyncio.run(seed(
        reset=os.environ.get("RESET_DEMO_DATA", "true").lower() != "false",
        credentials_file=os.environ.get("SEED_CREDENTIALS_FILE") or None,
    ))
