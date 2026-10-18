"""What the ESP32 can send that the backend must reject cleanly (4xx), never crash on (500).

Scenario IDs (BE-xx, X-xx, FUS-xx) refer to docs/designs/edge-cloud-scenarios.md. Tests marked
xfail(strict=True) assert the correct behavior for a known bug; they flip to a failure (XPASS)
the moment the bug is fixed, so the marker has to be removed together with the fix.
"""
from __future__ import annotations

import pytest

from app.geo import GeoJSONPoint
from app.models.route import Route
from tests.conftest import auth_headers, fusion_alert_body, status_body


@pytest.mark.parametrize("bad_id", ["CHANGE_ME", "123"])
async def test_alert_with_malformed_id_route_is_404_not_500(api_client, rig_a, bad_id):
    resp = await api_client.post(
        "/api/alerts", json=fusion_alert_body(bad_id), headers=rig_a.headers
    )
    assert resp.status_code in (404, 422)


async def test_alert_with_empty_id_route_is_404(api_client, rig_a):
    resp = await api_client.post("/api/alerts", json=fusion_alert_body(""), headers=rig_a.headers)
    assert resp.status_code == 404


async def test_status_on_malformed_route_path_id_is_404(api_client, rig_a):
    resp = await api_client.post(
        "/api/routes/CHANGE_ME/status", json=status_body(), headers=rig_a.headers
    )
    assert resp.status_code == 404


async def test_route_with_malformed_id_truck_does_not_break_active_routes(
    api_client, root_admin, rig_a
):
    await Route(
        id_driver="not-an-id",
        id_truck="not-an-id",
        origin_name="X",
        destination_name="Y",
        destination_coordinates=GeoJSONPoint(coordinates=(-99.0, 19.0)),
        estimated_departure="2030-01-01T08:00:00Z",
        operative_status="in_progress",
    ).insert()
    resp = await api_client.get("/api/routes/active", headers=auth_headers(root_admin))
    assert resp.status_code == 200
    assert rig_a.route_id in [r["id_route"] for r in resp.json()]


async def test_create_route_rejects_nonexistent_truck_and_driver_ids(api_client, root_admin):
    resp = await api_client.post(
        "/api/routes",
        json={
            "id_driver": "64b0a00000000000000000ff",
            "id_truck": "64b0a00000000000000000fe",
            "origin_name": "GDL",
            "destination_name": "CDMX",
            "destination_coordinates": {"lat": 19.43, "lon": -99.13},
            "estimated_departure": "2030-01-01T08:00:00Z",
        },
        headers=auth_headers(root_admin),
    )
    assert resp.status_code in (404, 422)


BAD_COORDINATES = [
    {"lat": 91.0, "lon": -103.0},
    {"lat": -91.0, "lon": -103.0},
    {"lat": 20.0, "lon": 181.0},
    {"lat": 20.0, "lon": -181.0},
]


@pytest.mark.parametrize("coords", BAD_COORDINATES)
async def test_alert_with_out_of_range_coordinates_is_422(api_client, rig_a, coords):
    resp = await api_client.post(
        "/api/alerts",
        json=fusion_alert_body(rig_a.route_id, coordinates=coords),
        headers=rig_a.headers,
    )
    assert resp.status_code == 422


@pytest.mark.parametrize("coords", BAD_COORDINATES)
async def test_status_with_out_of_range_coordinates_is_422(api_client, rig_a, coords):
    resp = await api_client.post(
        f"/api/routes/{rig_a.route_id}/status",
        json=status_body(current_coordinates=coords),
        headers=rig_a.headers,
    )
    assert resp.status_code == 422


@pytest.mark.parametrize("raw_value", ["NaN", "Infinity", "-Infinity"])
async def test_non_finite_coordinates_are_422(api_client, rig_a, raw_value):
    # json= can't encode NaN, so send the raw text the way a sloppy client would.
    body = (
        '{"current_coordinates": {"lat": %s, "lon": -103.0}, "current_speed": 0,'
        ' "odometer": 0, "vigilance": "low"}' % raw_value
    )
    resp = await api_client.post(
        f"/api/routes/{rig_a.route_id}/status",
        content=body,
        headers={**rig_a.headers, "Content-Type": "application/json"},
    )
    assert resp.status_code == 422


@pytest.mark.parametrize(
    "overrides", [{"current_speed": -5.0}, {"odometer": -1.0}], ids=["speed", "odometer"]
)
async def test_negative_speed_and_odometer_are_422(api_client, rig_a, overrides):
    resp = await api_client.post(
        f"/api/routes/{rig_a.route_id}/status",
        json=status_body(**overrides),
        headers=rig_a.headers,
    )
    assert resp.status_code == 422


async def test_negative_speed_at_event_is_422(api_client, rig_a):
    resp = await api_client.post(
        "/api/alerts",
        json=fusion_alert_body(rig_a.route_id, speed_at_event=-1.0),
        headers=rig_a.headers,
    )
    assert resp.status_code == 422


@pytest.mark.parametrize("missing", ["coordinates", "speed_at_event", "severity_level", "id_route"])
async def test_alert_missing_required_field_is_422(api_client, rig_a, missing):
    body = fusion_alert_body(rig_a.route_id)
    del body[missing]
    resp = await api_client.post("/api/alerts", json=body, headers=rig_a.headers)
    assert resp.status_code == 422


@pytest.mark.parametrize(
    "overrides",
    [{"severity_level": "high"}, {"source": "camera"}, {"grip_status": "loose"}],
    ids=["severity", "source", "grip"],
)
async def test_alert_unknown_enum_value_is_422(api_client, rig_a, overrides):
    resp = await api_client.post(
        "/api/alerts", json=fusion_alert_body(rig_a.route_id, **overrides), headers=rig_a.headers
    )
    assert resp.status_code == 422


async def test_status_unknown_vigilance_is_422(api_client, rig_a):
    resp = await api_client.post(
        f"/api/routes/{rig_a.route_id}/status",
        json=status_body(vigilance="none"),
        headers=rig_a.headers,
    )
    assert resp.status_code == 422


async def test_fusion_alert_without_grip_status_is_422(api_client, rig_a):
    body = fusion_alert_body(rig_a.route_id)
    del body["grip_status"]
    resp = await api_client.post("/api/alerts", json=body, headers=rig_a.headers)
    assert resp.status_code == 422


async def _post_alert(api_client, rig, **overrides) -> dict:
    resp = await api_client.post(
        "/api/alerts", json=fusion_alert_body(rig.route_id, **overrides), headers=rig.headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_related_alert_id_empty_string_stored_as_null(api_client, rig_a):
    alert = await _post_alert(api_client, rig_a, related_alert_id="")
    assert alert["related_alert_id"] is None


async def test_related_alert_id_must_exist(api_client, rig_a):
    resp = await api_client.post(
        "/api/alerts",
        json=fusion_alert_body(rig_a.route_id, related_alert_id="64b0a00000000000000000aa"),
        headers=rig_a.headers,
    )
    assert resp.status_code == 422


async def test_related_alert_id_must_be_on_the_same_route(api_client, rig_a, rig_b):
    other = await _post_alert(api_client, rig_b)
    resp = await api_client.post(
        "/api/alerts",
        json=fusion_alert_body(rig_a.route_id, related_alert_id=other["id_alert"]),
        headers=rig_a.headers,
    )
    assert resp.status_code == 422


async def test_related_alert_id_on_the_same_route_is_accepted(api_client, rig_a):
    original = await _post_alert(api_client, rig_a, severity_level="medium")
    escalation = await _post_alert(api_client, rig_a, related_alert_id=original["id_alert"])
    assert escalation["related_alert_id"] == original["id_alert"]
