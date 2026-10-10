#!/usr/bin/env python3
"""Manual smoke-check script for backend-argus.

NOT part of backend-argus's own hermetic pytest suite (src/backend-argus/tests/, which runs
against mongomock-motor, no real service needed). This script does the opposite: it hits a
real, already-running backend-argus + MongoDB stack over the network and prints a readable
PASS/FAIL log per check, so you can `docker compose up --build` and then run this to actually
confirm the connection works -- not just that the unit tests pass against a mock.

Confirms, end to end against the real API:
  1. the service is up (GET /health)
  2. a user can log in and read their own profile (POST /api/auth/login, GET /api/auth/me)
  3. the live-ops read path works (GET /api/routes/active)
  4. the device-API-key contract src/esp32-argus's firmware actually uses works: creates a
     throwaway truck/driver/route, rotates a real device key for that truck, then uses that
     key (not a user token) to POST a route-status update and a panic-button alert -- the
     exact two calls backend_api.cpp makes -- before cleaning everything back up.

Zero third-party dependencies -- stdlib only (urllib/json/hashlib), so there's nothing to
`pip install` before running this.

Usage:
    python src/tests/smoke_backend.py
    ARGUS_BACKEND_URL=http://localhost:8000 python src/tests/smoke_backend.py

Env vars (all optional):
    ARGUS_BACKEND_URL           default http://localhost:8000 (the root docker-compose.yml's
                                 default port mapping for backend-argus)
    ARGUS_ROOT_ADMIN_EMAIL       default admin@argus.dev   (app/auth/bootstrap.py's default)
    ARGUS_ROOT_ADMIN_PASSWORD    default changeme123       (same -- raw password, this script
                                 computes the SHA-256 digest the backend actually expects)

Exits 0 if every check passes, 1 otherwise.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

BASE_URL = os.environ.get("ARGUS_BACKEND_URL", "http://localhost:8000")
ROOT_ADMIN_EMAIL = os.environ.get("ARGUS_ROOT_ADMIN_EMAIL", "admin@argus.dev")
ROOT_ADMIN_PASSWORD = os.environ.get("ARGUS_ROOT_ADMIN_PASSWORD", "changeme123")

_FAILURES: list[str] = []


def _log(ok: bool, label: str, detail: str = "") -> None:
    tag = "[ OK ]" if ok else "[FAIL]"
    print(f"{tag} {label}" + (f" -- {detail}" if detail else ""))
    if not ok:
        _FAILURES.append(label)


def sha256_hex(raw_password: str) -> str:
    """What ui-argus's crypto.subtle.digest does client-side before a real login -- see
    src/backend-argus/CLAUDE.md's "Auth design": every password field on this API's boundary
    carries this digest, never the raw password."""
    return hashlib.sha256(raw_password.encode()).hexdigest()


def request(
    method: str,
    path: str,
    *,
    token: str | None = None,
    device_key: str | None = None,
    body: dict | None = None,
) -> tuple[int, object]:
    """Returns (status_code, parsed_json_or_text). status_code is -1 on a connection failure
    (service unreachable), not an HTTP status, so callers can tell the two apart if needed."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE_URL + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if device_key:
        req.add_header("X-Device-Api-Key", device_key)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw.decode(errors="replace")
    except urllib.error.URLError as exc:
        return -1, str(exc.reason)


def main() -> int:
    print(f"Argus backend smoke check -- {BASE_URL}\n")

    status, _ = request("GET", "/health")
    _log(status == 200, "GET /health", f"status={status}")
    if status != 200:
        _log(False, "aborting early -- the backend isn't reachable at all")
        return _finish()

    status, body = request(
        "POST",
        "/api/auth/login",
        body={"email": ROOT_ADMIN_EMAIL, "password": sha256_hex(ROOT_ADMIN_PASSWORD)},
    )
    _log(status == 200, "POST /api/auth/login", f"status={status}")
    token = body.get("access_token") if status == 200 and isinstance(body, dict) else None
    if not token:
        _log(False, "aborting -- no access token, every later check needs one")
        return _finish()

    status, me = request("GET", "/api/auth/me", token=token)
    role = me.get("role") if isinstance(me, dict) else "?"
    _log(status == 200, "GET /api/auth/me", f"status={status} role={role}")

    status, routes = request("GET", "/api/routes/active", token=token)
    count = len(routes) if isinstance(routes, list) else "?"
    _log(status == 200, "GET /api/routes/active", f"status={status} count={count}")

    # --- Device-API-key round trip: the exact contract src/esp32-argus's firmware uses ---
    suffix = str(int(time.time()))
    truck_id = driver_id = route_id = device_key = None

    status, truck = request(
        "POST",
        "/api/trucks",
        token=token,
        body={
            "plate_number": f"SMOKE-{suffix}",
            "brand": "SmokeTest",
            "model": "SmokeTest",
            "company_number": f"SMOKE-CO-{suffix}",
        },
    )
    _log(status == 201, "POST /api/trucks (throwaway)", f"status={status}")
    truck_id = truck.get("id_truck") if status == 201 and isinstance(truck, dict) else None

    if truck_id:
        status, key_body = request("POST", f"/api/trucks/{truck_id}/rotate-key", token=token)
        _log(status == 200, "POST /api/trucks/{id}/rotate-key", f"status={status}")
        if status == 200 and isinstance(key_body, dict):
            device_key = key_body.get("device_api_key")

    if truck_id:
        status, driver = request(
            "POST",
            "/api/drivers",
            token=token,
            body={
                "first_name": "Smoke",
                "last_name": "Test",
                "license_number": f"SMOKE-{suffix}",
                "license_expiration": "2030-01-01",
                "phone_number": "+00-000-0000",
                "emergency_contact_name": "Smoke Contact",
                "emergency_contact_phone": "+00-000-0001",
                "blood_type": "O+",
            },
        )
        _log(status == 201, "POST /api/drivers (throwaway)", f"status={status}")
        driver_id = driver.get("id_driver") if status == 201 and isinstance(driver, dict) else None

    if truck_id and driver_id:
        status, route = request(
            "POST",
            "/api/routes",
            token=token,
            body={
                "id_driver": driver_id,
                "id_truck": truck_id,
                "origin_name": "Smoke Origin",
                "destination_name": "Smoke Destination",
                "destination_coordinates": {"lat": 19.4326, "lon": -99.1332},
                "estimated_departure": datetime.now(timezone.utc).isoformat(),
            },
        )
        _log(status == 201, "POST /api/routes (throwaway)", f"status={status}")
        route_id = route.get("id_route") if status == 201 and isinstance(route, dict) else None

    if route_id and device_key:
        status, _ = request(
            "POST",
            f"/api/routes/{route_id}/status",
            device_key=device_key,
            body={
                "current_coordinates": {"lat": 19.4326, "lon": -99.1332},
                "current_speed": 80.0,
                "odometer": 12345.6,
                "vigilance": "low",
            },
        )
        _log(status == 201, "POST /api/routes/{id}/status (device key)", f"status={status}")

        status, _ = request(
            "POST",
            "/api/alerts",
            device_key=device_key,
            body={
                "id_route": route_id,
                "alert_type": "panic_button",
                "severity_level": "critical",
                "source": "panic_button",
                "coordinates": {"lat": 19.4326, "lon": -99.1332},
                "speed_at_event": 80.0,
            },
        )
        _log(status == 201, "POST /api/alerts (device key, panic_button)", f"status={status}")
    else:
        _log(False, "device-key round trip", "skipped -- an earlier step didn't produce a "
             "truck/driver/route/device_key")

    # --- Cleanup -- keep this script non-destructive/idempotent to re-run ---
    if route_id:
        status, _ = request("DELETE", f"/api/routes/{route_id}", token=token)
        _log(status == 200, "DELETE /api/routes/{id} (cleanup)", f"status={status}")
    if driver_id:
        status, _ = request("DELETE", f"/api/drivers/{driver_id}", token=token)
        _log(status == 200, "DELETE /api/drivers/{id} (cleanup)", f"status={status}")
    if truck_id:
        status, _ = request("DELETE", f"/api/trucks/{truck_id}", token=token)
        _log(status == 200, "DELETE /api/trucks/{id} (cleanup)", f"status={status}")

    return _finish()


def _finish() -> int:
    print()
    if _FAILURES:
        print(f"{len(_FAILURES)} check(s) failed:")
        for f in _FAILURES:
            print(f"  - {f}")
        return 1
    print("All checks passed -- the backend is reachable and the device-key contract works.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
