"""Unit tests for BackendClient, against a mocked transport -- no real backend/network needed.

The things worth asserting mechanically, not just by inspection: (1) a login request never
carries the raw password, matching backend-argus's Sha256HexDigest contract; (2) the device
report uses X-Device-Api-Key and never an Authorization header (the report endpoint takes a device
key only, contracts C2), so a simulated truck can't accidentally rely on the admin JWT for its
ongoing traffic; (3) the report call hands every HTTP status back to the sender instead of
raising, since each one means something different (C2.3).
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


_REPORT_BODY = {
    "points": [
        {
            "age_s": 4.0,
            "lat": 20.6597,
            "lon": -103.3496,
            "speed": 62.5,
            "vigilance": "low",
            "last_known": False,
            "location_age_s": 0,
        }
    ]
}


async def test_post_report_uses_device_header_not_bearer():
    captured: list[httpx.Request] = []
    transport = _mock_transport(
        captured,
        {
            "points": {"stored": 1, "route": "r1", "status": "ok"},
            "event": None,
            "active_route": "r1",
        },
    )
    client = BackendClient("http://backend", transport=transport)

    response = await client.post_report("device-key", _REPORT_BODY)
    await client.aclose()

    request = captured[0]
    assert request.method == "POST"
    assert request.url.path == "/api/devices/me/report"
    assert request.headers["x-device-api-key"] == "device-key"
    assert "authorization" not in request.headers
    assert json.loads(request.content) == _REPORT_BODY
    assert response.status_code == 200
    assert response.body["active_route"] == "r1"


@pytest.mark.parametrize("status_code", [401, 422, 429, 503])
async def test_post_report_returns_error_statuses_instead_of_raising(status_code):
    # Every status means something to the sender (C2.3), so the client must hand it back.
    captured: list[httpx.Request] = []
    transport = _mock_transport(captured, {"detail": "nope"}, status_code=status_code)
    client = BackendClient("http://backend", transport=transport)

    response = await client.post_report("device-key", _REPORT_BODY)
    await client.aclose()

    assert response.status_code == status_code
    assert response.body == {"detail": "nope"}


async def test_post_report_tolerates_a_non_json_body():
    transport = httpx.MockTransport(lambda request: httpx.Response(502, text="Bad Gateway"))
    client = BackendClient("http://backend", transport=transport)

    response = await client.post_report("device-key", _REPORT_BODY)
    await client.aclose()

    assert response.status_code == 502
    assert response.body is None


async def test_post_report_lets_transport_errors_raise():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = BackendClient("http://backend", transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.TransportError):
        await client.post_report("device-key", _REPORT_BODY)
    await client.aclose()


async def test_post_report_gives_up_after_8_s_like_the_esp32_on_wifi():
    # ATTEMPT_TIMEOUT_WIFI_MS = 8000 (contracts C4), not the client's 10 s default.
    captured: list[httpx.Request] = []
    transport = _mock_transport(captured, {"points": {}, "event": None, "active_route": None})
    client = BackendClient("http://backend", transport=transport)

    await client.post_report("device-key", _REPORT_BODY)
    await client.aclose()

    assert captured[0].extensions["timeout"] == {
        "connect": 8.0,
        "read": 8.0,
        "write": 8.0,
        "pool": 8.0,
    }


def test_legacy_device_writes_are_gone():
    # The report endpoint is the only device write (C2): no status ping, no alert create, no
    # device resolve (the driver OK is an event in the report now).
    for name in ("post_status", "post_alert", "resolve_alert"):
        assert not hasattr(BackendClient, name)
