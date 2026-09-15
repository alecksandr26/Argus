import pytest

from tests.conftest import auth_headers


@pytest.mark.asyncio
async def test_guardian_cannot_create_truck(api_client, guardian):
    resp = await api_client.post(
        "/api/trucks",
        json={"plate_number": "X-1", "brand": "B", "model": "M", "company_number": "C-1"},
        headers=auth_headers(guardian),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_root_admin_can_create_truck(api_client, root_admin):
    resp = await api_client.post(
        "/api/trucks",
        json={"plate_number": "X-2", "brand": "B", "model": "M", "company_number": "C-2"},
        headers=auth_headers(root_admin),
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_truck_driver_cannot_list_users(api_client, truck_driver_user):
    resp = await api_client.get("/api/users", headers=auth_headers(truck_driver_user))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_any_authenticated_role_can_read_trucks(api_client, truck_driver_user, sample_truck):
    resp = await api_client.get("/api/trucks", headers=auth_headers(truck_driver_user))
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_guardian_can_review_alert_but_not_delete(api_client, guardian, root_admin):
    # Seed a route + alert directly via the API as root_admin, then have guardian review it.
    truck_resp = await api_client.post(
        "/api/trucks",
        json={"plate_number": "X-3", "brand": "B", "model": "M", "company_number": "C-3"},
        headers=auth_headers(root_admin),
    )
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
        headers=auth_headers(root_admin),
    )
    route_resp = await api_client.post(
        "/api/routes",
        json={
            "id_driver": driver_resp.json()["id_driver"],
            "id_truck": truck_resp.json()["id_truck"],
            "origin_name": "A",
            "destination_name": "B",
            "destination_coordinates": {"lat": 19.0, "lon": -99.0},
            "estimated_departure": "2030-01-01T00:00:00Z",
        },
        headers=auth_headers(root_admin),
    )
    alert_resp = await api_client.post(
        "/api/alerts",
        json={
            "id_route": route_resp.json()["id_route"],
            "alert_type": "drowsiness",
            "severity_level": "medium",
            "source": "fusion",
            "ai_metadata": {"scores": {"not_drowsy": 0.2, "drowsy": 0.8}},
            "grip_status": "good",
            "coordinates": {"lat": 19.1, "lon": -99.1},
            "speed_at_event": 80.0,
        },
        headers=auth_headers(root_admin),
    )
    assert alert_resp.status_code == 201
    alert_id = alert_resp.json()["id_alert"]

    review_resp = await api_client.put(
        f"/api/alerts/{alert_id}",
        json={"reviewed_by_operator": True, "operator_notes": "Checked, false positive"},
        headers=auth_headers(guardian),
    )
    assert review_resp.status_code == 200
    assert review_resp.json()["reviewed_by_operator"] is True

    delete_resp = await api_client.delete(f"/api/alerts/{alert_id}", headers=auth_headers(guardian))
    assert delete_resp.status_code == 403
