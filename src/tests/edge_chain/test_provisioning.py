"""Provisioning a truck end to end: the dashboard issues a device key, the Pi holds it (its
argus-device.env, i.e. a cv-argus DeviceConfig), the ESP32 fetches it over Bluetooth (CONFIG),
finds its route from the key alone, and relays with it. Rotating or revoking the key in the
dashboard locks the old one out.

Same real-code-at-every-hop setup as test_edge_chain.py; see this directory's README.md.
"""
from __future__ import annotations

import pytest

from cv_argus.alerts import route_status
from cv_argus.buffer import open_buffer
from cv_argus.sender import NOCONFIG_LINE, DeviceConfig, FakeListener, SenderServer

from .esp32_sim import SimulatedEsp32, fetch_config, resolve_active_route

pytestmark = pytest.mark.asyncio

BACKEND_URL = "https://argus.example.com"


@pytest.fixture
def make_pi(tmp_path):
    """A cv-argus Pi (Buffer + SenderServer) started with a given argus-device.env."""
    started = []

    def _make(env: dict[str, str]):
        buffer = open_buffer(tmp_path / f"buffer{len(started)}.sqlite3")
        listener = FakeListener()
        server = SenderServer(
            buffer, listener, accept_timeout=0.1, device_config=DeviceConfig.from_env(env)
        )
        server.start()
        started.append((server, buffer))
        return buffer, listener

    yield _make
    for server, buffer in started:
        server.stop()
        server.join(timeout=2.0)
        buffer.close()


async def _new_truck_on_a_route(backend) -> tuple[str, str]:
    truck = (
        await backend.post(
            "/api/trucks",
            json={"plate_number": "PROV-001", "brand": "Volvo", "model": "FH", "company_number": "P-1"},
        )
    ).json()
    driver = (
        await backend.post(
            "/api/drivers",
            json={
                "first_name": "Prov",
                "last_name": "Driver",
                "license_number": "LIC-PROV",
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
                "operative_status": "in_progress",
            },
        )
    ).json()
    return truck["id_truck"], route["id_route"]


async def _issue_env(backend, truck_id: str) -> dict[str, str]:
    """What the Fleet screen's "Generate device credentials" downloads as argus-device.env."""
    key = (await backend.post(f"/api/trucks/{truck_id}/rotate-key")).json()["device_api_key"]
    return {
        "ARGUS_TRUCK_ID": truck_id,
        "ARGUS_DEVICE_API_KEY": key,
        "ARGUS_BACKEND_URL": BACKEND_URL,
    }


async def test_dashboard_key_reaches_the_esp32_and_authorizes_its_relay(
    backend, device_http, make_pi, clock
):
    truck_id, route_id = await _new_truck_on_a_route(backend)
    env = await _issue_env(backend, truck_id)
    buffer, listener = make_pi(env)

    creds = fetch_config(listener)
    assert creds == {
        "truck_id": truck_id,
        "device_api_key": env["ARGUS_DEVICE_API_KEY"],
        "backend_url": BACKEND_URL,
    }
    # No route id configured anywhere: the ESP32 finds it from the key.
    assert await resolve_active_route(device_http, creds["device_api_key"]) == route_id

    dev = SimulatedEsp32(
        listener=listener,
        http=device_http,
        device_key=creds["device_api_key"],
        route_id=route_id,
        clock=clock,
    )
    buffer.enqueue(route_status("cam-0"))
    result = await dev.poll()
    assert (result.pulled, len(result.acked), result.protocol_ok) == (1, 1, True)
    assert (await backend.get(f"/api/routes/{route_id}/status")).status_code == 200

    truck = (await backend.get(f"/api/trucks/{truck_id}")).json()
    assert truck["has_device_key"] is True
    assert truck["device_last_seen_at"] is not None


async def test_unprovisioned_pi_answers_noconfig(make_pi):
    _, listener = make_pi({})
    assert fetch_config(listener) == NOCONFIG_LINE


async def test_rotation_locks_out_the_old_key_until_the_pi_gets_the_new_env(
    backend, device_http, make_pi
):
    truck_id, route_id = await _new_truck_on_a_route(backend)
    old_env = await _issue_env(backend, truck_id)
    new_env = await _issue_env(backend, truck_id)  # operator clicks "Rotate key"

    # The Pi still runs with the old file: the ESP32's lookups 401 (it would re-ask CONFIG)...
    _, old_pi = make_pi(old_env)
    stale = fetch_config(old_pi)
    lookup = await device_http.get(
        "/api/devices/me/active-route", headers={"X-Device-Api-Key": stale["device_api_key"]}
    )
    assert lookup.status_code == 401

    # ...until the new argus-device.env is installed and the container restarted.
    _, new_pi = make_pi(new_env)
    fresh = fetch_config(new_pi)
    assert await resolve_active_route(device_http, fresh["device_api_key"]) == route_id


async def test_revoked_key_is_rejected(backend, device_http, make_pi):
    truck_id, _ = await _new_truck_on_a_route(backend)
    env = await _issue_env(backend, truck_id)
    _, listener = make_pi(env)
    creds = fetch_config(listener)

    assert (await backend.delete(f"/api/trucks/{truck_id}/device-key")).status_code == 200
    lookup = await device_http.get(
        "/api/devices/me/active-route", headers={"X-Device-Api-Key": creds["device_api_key"]}
    )
    assert lookup.status_code == 401
