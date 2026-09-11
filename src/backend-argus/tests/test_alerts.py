import pytest

from tests.conftest import auth_headers


async def _make_route(api_client, headers, truck_id) -> str:
    driver_resp = await api_client.post(
        "/api/drivers",
        json={
            "first_name": "A",
            "last_name": "B",
            "license_number": "L-1",
            "license_expiration": "2030-01-01",
            "phone_number": "+1-555-1111",
            "emergency_contact_name": "C",
            "emergency_contact_phone": "+1-555-2222",
            "blood_type": "O+",
        },
        headers=headers,
    )
    route_resp = await api_client.post(
        "/api/routes",
        json={
            "id_driver": driver_resp.json()["id_driver"],
            "id_truck": truck_id,
            "origin_name": "CDMX",
            "destination_name": "GDL",
            "destination_coordinates": {"lat": 20.0, "lon": -103.0},
            "estimated_departure": "2030-01-01T08:00:00Z",
            "operative_status": "in_progress",
        },
        headers=headers,
    )
    return route_resp.json()["id_route"]


@pytest.mark.asyncio
async def test_create_alert_with_nullable_ai_metadata_fields(api_client, root_admin, sample_truck):
    """Mirrors cv-argus's real envelope: no `model` version string, no `clip_seconds` — see the
    backend CLAUDE.md's "Coordination note" for why these are optional, not required."""
    headers = auth_headers(root_admin)
    route_id = await _make_route(api_client, headers, str(sample_truck.id))

    resp = await api_client.post(
        "/api/alerts",
        json={
            "id_route": route_id,
            "alert_type": "drowsiness",
            "severity_level": "critical",
            "ai_metadata": {"scores": {"not_drowsy": 0.12, "drowsy": 0.88}},
            "coordinates": {"lat": 19.1, "lon": -99.1},
            "speed_at_event": 95.0,
        },
        headers=headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["ai_metadata"]["model"] is None
    assert body["ai_metadata"]["clip_seconds"] is None
    assert body["reviewed_by_operator"] is False


@pytest.mark.asyncio
async def test_device_key_can_post_alert(api_client, root_admin, sample_truck):
    headers = auth_headers(root_admin)
    key_resp = await api_client.post(f"/api/trucks/{sample_truck.id}/rotate-key", headers=headers)
    device_key = key_resp.json()["device_api_key"]
    route_id = await _make_route(api_client, headers, str(sample_truck.id))

    resp = await api_client.post(
        "/api/alerts",
        json={
            "id_route": route_id,
            "alert_type": "drowsiness",
            "severity_level": "critical",
            "ai_metadata": {"scores": {"not_drowsy": 0.05, "drowsy": 0.95}},
            "coordinates": {"lat": 19.1, "lon": -99.1},
            "speed_at_event": 95.0,
        },
        headers={"X-Device-Api-Key": device_key},
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_list_alerts_filters_by_severity(api_client, root_admin, sample_truck):
    headers = auth_headers(root_admin)
    route_id = await _make_route(api_client, headers, str(sample_truck.id))

    for severity in ("critical", "low"):
        await api_client.post(
            "/api/alerts",
            json={
                "id_route": route_id,
                "alert_type": "drowsiness",
                "severity_level": severity,
                "ai_metadata": {"scores": {"not_drowsy": 0.5, "drowsy": 0.5}},
                "coordinates": {"lat": 19.1, "lon": -99.1},
                "speed_at_event": 60.0,
            },
            headers=headers,
        )

    resp = await api_client.get("/api/alerts", params={"severity": "critical"}, headers=headers)
    assert resp.status_code == 200
    results = resp.json()
    assert len(results) == 1
    assert results[0]["severity_level"] == "critical"
