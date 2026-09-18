"""Unit tests for BackendClient, against a mocked transport -- no real backend/network needed.

The two things worth asserting mechanically, not just by inspection: (1) a login request never
carries the raw password, matching backend-argus's Sha256HexDigest contract; (2) device-key
writes use X-Device-Api-Key and never an Authorization header, matching authorize_device_or_user
so a simulated truck can't accidentally rely on the admin JWT for its ongoing traffic.
"""
from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from simulator_argus.client import BackendClient, sha256_hex


def _mock_transport(captured: list[httpx.Request], response_body: dict, status_code: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(status_code, json=response_body)

    return httpx.MockTransport(handler)


async def test_sha256_hex_matches_stdlib():
    assert sha256_hex("changeme123") == hashlib.sha256(b"changeme123").hexdigest()


async def test_login_sends_digest_not_raw_password():
    captured: list[httpx.Request] = []
    transport = _mock_transport(
        captured, {"access_token": "tok", "token_type": "bearer", "user": {}}
    )
    client = BackendClient("http://backend", transport=transport)

    token = await client.login("admin@argus.dev", "changeme123")
    await client.aclose()

    assert token == "tok"
    sent = json.loads(captured[0].content)
    assert sent["password"] == hashlib.sha256(b"changeme123").hexdigest()
    assert sent["password"] != "changeme123"


async def test_rotate_key_uses_bearer_token_and_returns_plaintext_key():
    captured: list[httpx.Request] = []
    transport = _mock_transport(captured, {"id_truck": "abc", "device_api_key": "secret-key"})
    client = BackendClient("http://backend", transport=transport)

    key = await client.rotate_key("tok", "abc")
    await client.aclose()

    assert key == "secret-key"
    assert captured[0].headers["authorization"] == "Bearer tok"
    assert captured[0].url.path == "/api/trucks/abc/rotate-key"


async def test_post_status_uses_device_header_not_bearer():
    captured: list[httpx.Request] = []
    transport = _mock_transport(captured, {"id_status_route": "s1"})
    client = BackendClient("http://backend", transport=transport)

    await client.post_status(
        "device-key",
        "route1",
        {
            "current_coordinates": {"lat": 1.0, "lon": 2.0},
            "current_speed": 80.0,
            "odometer": 10.0,
            "vigilance": "low",
        },
    )
    await client.aclose()

    request = captured[0]
    assert request.headers["x-device-api-key"] == "device-key"
    assert "authorization" not in request.headers
    assert request.url.path == "/api/routes/route1/status"


async def test_post_alert_panic_shape_omits_ai_metadata_and_grip_status():
    captured: list[httpx.Request] = []
    transport = _mock_transport(captured, {"id_alert": "a1"})
    client = BackendClient("http://backend", transport=transport)

    await client.post_alert(
        "device-key",
        {
            "id_route": "r1",
            "alert_type": "panic_button",
            "severity_level": "critical",
            "source": "panic_button",
            "ai_metadata": None,
            "grip_status": None,
            "coordinates": {"lat": 1.0, "lon": 2.0},
            "speed_at_event": 0.0,
        },
    )
    await client.aclose()

    body = json.loads(captured[0].content)
    assert body["source"] == "panic_button"
    assert body["ai_metadata"] is None
    assert body["grip_status"] is None


async def test_resolve_alert_sends_only_resolved_at():
    captured: list[httpx.Request] = []
    transport = _mock_transport(captured, {"id_alert": "a1", "resolved_at": "2026-01-01T00:00:00Z"})
    client = BackendClient("http://backend", transport=transport)

    await client.resolve_alert("device-key", "a1", "2026-01-01T00:00:00+00:00")
    await client.aclose()

    body = json.loads(captured[0].content)
    assert set(body.keys()) == {"resolved_at"}
    assert captured[0].headers["x-device-api-key"] == "device-key"
