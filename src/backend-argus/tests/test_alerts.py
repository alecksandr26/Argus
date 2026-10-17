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
            "source": "fusion",
            "ai_metadata": {"scores": {"not_drowsy": 0.12, "drowsy": 0.88}},
            "grip_status": "bad",
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
            "source": "fusion",
            "ai_metadata": {"scores": {"not_drowsy": 0.05, "drowsy": 0.95}},
            "grip_status": "bad",
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
                "source": "fusion",
                "ai_metadata": {"scores": {"not_drowsy": 0.5, "drowsy": 0.5}},
                "grip_status": "good",
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


@pytest.mark.asyncio
async def test_panic_button_alert_has_no_ai_metadata_or_grip_status(
    api_client, root_admin, sample_truck
):
    headers = auth_headers(root_admin)
    route_id = await _make_route(api_client, headers, str(sample_truck.id))

    resp = await api_client.post(
        "/api/alerts",
        json={
            "id_route": route_id,
            "alert_type": "Panic button pressed",
            "severity_level": "critical",
            "source": "panic_button",
            "coordinates": {"lat": 19.1, "lon": -99.1},
            "speed_at_event": 90.0,
        },
        headers=headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["ai_metadata"] is None
    assert body["grip_status"] is None
    assert body["source"] == "panic_button"


@pytest.mark.asyncio
async def test_fusion_alert_without_ai_metadata_is_rejected(api_client, root_admin, sample_truck):
    """The two branches of validate_source_ai_metadata (app/models/alert.py): `fusion` requires
    both ai_metadata and grip_status."""
    headers = auth_headers(root_admin)
    route_id = await _make_route(api_client, headers, str(sample_truck.id))

    resp = await api_client.post(
        "/api/alerts",
        json={
            "id_route": route_id,
            "alert_type": "drowsiness",
            "severity_level": "medium",
            "source": "fusion",
            "coordinates": {"lat": 19.1, "lon": -99.1},
            "speed_at_event": 90.0,
        },
        headers=headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_panic_button_alert_with_ai_metadata_is_rejected(
    api_client, root_admin, sample_truck
):
    headers = auth_headers(root_admin)
    route_id = await _make_route(api_client, headers, str(sample_truck.id))

    resp = await api_client.post(
        "/api/alerts",
        json={
            "id_route": route_id,
            "alert_type": "Panic button pressed",
            "severity_level": "critical",
            "source": "panic_button",
            "ai_metadata": {"scores": {"not_drowsy": 0.5, "drowsy": 0.5}},
            "coordinates": {"lat": 19.1, "lon": -99.1},
            "speed_at_event": 90.0,
        },
        headers=headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_escalation_links_to_original_alert(api_client, root_admin, sample_truck):
    """Escalation creates a new, linked Alert row rather than mutating the original in place —
    see fusion_contract.py's AlertToRaise.related_alert_id."""
    headers = auth_headers(root_admin)
    route_id = await _make_route(api_client, headers, str(sample_truck.id))

    medium_resp = await api_client.post(
        "/api/alerts",
        json={
            "id_route": route_id,
            "alert_type": "drowsiness",
            "severity_level": "medium",
            "source": "fusion",
            "ai_metadata": {"scores": {"not_drowsy": 0.4, "drowsy": 0.6}},
            "grip_status": "good",
            "coordinates": {"lat": 19.1, "lon": -99.1},
            "speed_at_event": 90.0,
        },
        headers=headers,
    )
    medium_id = medium_resp.json()["id_alert"]

    critical_resp = await api_client.post(
        "/api/alerts",
        json={
            "id_route": route_id,
            "alert_type": "drowsiness",
            "severity_level": "critical",
            "source": "fusion",
            "ai_metadata": {"scores": {"not_drowsy": 0.1, "drowsy": 0.9}},
            "grip_status": "bad",
            "related_alert_id": medium_id,
            "coordinates": {"lat": 19.1, "lon": -99.1},
            "speed_at_event": 88.0,
        },
        headers=headers,
    )
    assert critical_resp.status_code == 201
    assert critical_resp.json()["related_alert_id"] == medium_id


@pytest.mark.asyncio
async def test_device_key_can_resolve_but_not_review(api_client, root_admin, sample_truck):
    headers = auth_headers(root_admin)
    key_resp = await api_client.post(f"/api/trucks/{sample_truck.id}/rotate-key", headers=headers)
    device_key = key_resp.json()["device_api_key"]
    route_id = await _make_route(api_client, headers, str(sample_truck.id))

    alert_resp = await api_client.post(
        "/api/alerts",
        json={
            "id_route": route_id,
            "alert_type": "drowsiness",
            "severity_level": "medium",
            "source": "fusion",
            "ai_metadata": {"scores": {"not_drowsy": 0.4, "drowsy": 0.6}},
            "grip_status": "bad",
            "coordinates": {"lat": 19.1, "lon": -99.1},
            "speed_at_event": 90.0,
        },
        headers=headers,
    )
    alert_id = alert_resp.json()["id_alert"]

    resolve_resp = await api_client.put(
        f"/api/alerts/{alert_id}",
        json={"resolved_at": "2030-01-01T00:00:00Z"},
        headers={"X-Device-Api-Key": device_key},
    )
    assert resolve_resp.status_code == 200
    assert resolve_resp.json()["resolved_at"] is not None

    review_resp = await api_client.put(
        f"/api/alerts/{alert_id}",
        json={"reviewed_by_operator": True},
        headers={"X-Device-Api-Key": device_key},
    )
    assert review_resp.status_code == 403


async def _post_critical(api_client, headers, route_id) -> str:
    resp = await api_client.post(
        "/api/alerts",
        json={
            "id_route": route_id,
            "alert_type": "drowsiness",
            "severity_level": "critical",
            "source": "fusion",
            "ai_metadata": {"scores": {"not_drowsy": 0.1, "drowsy": 0.9}},
            "grip_status": "bad",
            "coordinates": {"lat": 19.1, "lon": -99.1},
            "speed_at_event": 80.0,
        },
        headers=headers,
    )
    return resp.json()["id_alert"]


@pytest.mark.asyncio
async def test_guardian_can_deescalate_and_original_is_kept(
    api_client, root_admin, guardian, sample_truck
):
    route_id = await _make_route(api_client, auth_headers(root_admin), str(sample_truck.id))
    alert_id = await _post_critical(api_client, auth_headers(root_admin), route_id)

    resp = await api_client.post(
        f"/api/alerts/{alert_id}/de-escalate",
        json={"severity_level": "medium", "reason": "Called driver, pulling over to rest"},
        headers=auth_headers(guardian),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["severity_level"] == "medium"
    assert body["original_severity_level"] == "critical"
    assert body["deescalated_by"] == str(guardian.id)
    assert body["deescalated_at"] is not None
    assert body["deescalation_reason"] == "Called driver, pulling over to rest"
    assert body["reviewed_by_operator"] is True

    # A second step down keeps the device-reported original, not the intermediate level.
    resp = await api_client.post(
        f"/api/alerts/{alert_id}/de-escalate",
        json={"severity_level": "low", "reason": "False positive confirmed"},
        headers=auth_headers(guardian),
    )
    assert resp.status_code == 200
    assert resp.json()["severity_level"] == "low"
    assert resp.json()["original_severity_level"] == "critical"


@pytest.mark.asyncio
async def test_deescalate_rejects_same_or_higher_severity(api_client, root_admin, sample_truck):
    headers = auth_headers(root_admin)
    route_id = await _make_route(api_client, headers, str(sample_truck.id))
    alert_id = await _post_critical(api_client, headers, route_id)

    resp = await api_client.post(
        f"/api/alerts/{alert_id}/de-escalate",
        json={"severity_level": "critical", "reason": "no-op"},
        headers=headers,
    )
    assert resp.status_code == 422

    resp = await api_client.post(
        f"/api/alerts/{alert_id}/de-escalate",
        json={"severity_level": "low", "reason": ""},
        headers=headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_deescalate_forbidden_for_admin_and_device_key(
    api_client, root_admin, admin_user, sample_truck
):
    headers = auth_headers(root_admin)
    key = (await api_client.post(f"/api/trucks/{sample_truck.id}/rotate-key", headers=headers)).json()[
        "device_api_key"
    ]
    route_id = await _make_route(api_client, headers, str(sample_truck.id))
    alert_id = await _post_critical(api_client, headers, route_id)
    body = {"severity_level": "low", "reason": "x"}

    resp = await api_client.post(
        f"/api/alerts/{alert_id}/de-escalate", json=body, headers=auth_headers(admin_user)
    )
    assert resp.status_code == 403

    resp = await api_client.post(
        f"/api/alerts/{alert_id}/de-escalate", json=body, headers={"X-Device-Api-Key": key}
    )
    assert resp.status_code == 401

    still = (await api_client.get(f"/api/alerts/{alert_id}", headers=headers)).json()
    assert still["severity_level"] == "critical"
    assert still["original_severity_level"] is None
