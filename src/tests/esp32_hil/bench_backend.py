"""backend-argus for bench tests against the real ESP32: the real FastAPI app (same routers,
same device-key auth), served by uvicorn on the LAN so the ESP32 can reach it over WiFi, but on
mongomock-motor instead of MongoDB. No Docker, no mongod; data lives only as long as the process.

Seeds one truck/driver/in-progress route with FIXED ids and a FIXED device key, so the values in
the firmware's config.h stay valid across runs and you don't reflash every time.

    python src/tests/esp32_hil/bench_backend.py            # serve on 0.0.0.0:8000 until Ctrl+C

Env vars: ARGUS_BENCH_PORT (default 8000), ARGUS_BENCH_DEVICE_KEY (default below).
Root admin login is backend-argus's own default (admin@argus.dev / changeme123), so
`smoke_backend.py` runs against this too.
"""
from __future__ import annotations

import os
import socket
import threading
import time
from datetime import date, datetime, timezone

import _imports  # noqa: F401  (path wiring, must come first)

import uvicorn
from mongomock_motor import AsyncMongoMockClient

import app.main as backend_main
from app.auth.security import hash_secret
from app.database import init_db as _real_init_db
from app.geo import GeoJSONPoint
from app.models.common import DriverStatus, RouteStatus, TruckStatus
from app.models.driver import Driver
from app.models.route import Route
from app.models.truck import Truck
from beanie import PydanticObjectId

TRUCK_ID = "64b0a0000000000000000001"
DRIVER_ID = "64b0a0000000000000000002"
ROUTE_ID = "64b0a0000000000000000003"
DEVICE_API_KEY = os.environ.get("ARGUS_BENCH_DEVICE_KEY", "argus-bench-device-key")
PORT = int(os.environ.get("ARGUS_BENCH_PORT", "8000"))


async def _seed() -> None:
    await Truck(
        id=PydanticObjectId(TRUCK_ID),
        plate_number="BENCH-001",
        brand="Kenworth",
        model="T680",
        company_number="UNIT-BENCH-001",
        operative_status=TruckStatus.ACTIVE,
        device_api_key_hash=hash_secret(DEVICE_API_KEY),
    ).insert()
    await Driver(
        id=PydanticObjectId(DRIVER_ID),
        first_name="Bench",
        last_name="Driver",
        license_number="LIC-BENCH",
        license_expiration=date(2030, 1, 1),
        phone_number="5550000000",
        emergency_contact_name="Contact",
        emergency_contact_phone="5550000001",
        blood_type="O+",
        operative_status=DriverStatus.ON_ROUTE,
    ).insert()
    await Route(
        id=PydanticObjectId(ROUTE_ID),
        id_driver=DRIVER_ID,
        id_truck=TRUCK_ID,
        origin_name="Guadalajara",
        destination_name="CDMX",
        destination_coordinates=GeoJSONPoint(coordinates=(-99.13, 19.43)),
        estimated_departure=datetime.now(timezone.utc),
        operative_status=RouteStatus.IN_PROGRESS,
    ).insert()


async def _init_db_in_memory(client=None):
    client = await _real_init_db(client=AsyncMongoMockClient())
    await _seed()
    return client


# app.main's lifespan calls init_db() by this name; swap in the in-memory one before startup.
backend_main.init_db = _init_db_in_memory


def lan_ip() -> str:
    """This machine's address on the network the ESP32 joins (the default-route interface)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("8.8.8.8", 80))  # UDP: no packet is sent, this only picks a route
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def config_h_values() -> str:
    return (
        f'#define BACKEND_BASE_URL "http://{lan_ip()}:{PORT}"\n'
        f'#define DEVICE_API_KEY "{DEVICE_API_KEY}"\n'
        f'#define ROUTE_ID "{ROUTE_ID}"'
    )


class BenchBackend:
    """uvicorn in a background thread. `start()` returns once the app has finished startup."""

    def __init__(self, host: str = "0.0.0.0", port: int = PORT) -> None:
        self.port = port
        self._server = uvicorn.Server(
            uvicorn.Config(backend_main.app, host=host, port=port, log_level="warning")
        )
        self._thread = threading.Thread(target=self._server.run, name="bench-backend", daemon=True)

    @property
    def local_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, timeout: float = 15.0) -> None:
        self._thread.start()
        deadline = time.monotonic() + timeout
        while not self._server.started:
            if not self._thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError(f"bench backend failed to start on port {self.port}")
            time.sleep(0.05)

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5.0)


if __name__ == "__main__":
    backend = BenchBackend()
    backend.start()
    print(f"bench backend-argus (in-memory) on http://0.0.0.0:{PORT}")
    print("Put these in src/esp32-argus/src/esp32_argus/config.h:\n")
    print(config_h_values())
    print("\nCtrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        backend.stop()
