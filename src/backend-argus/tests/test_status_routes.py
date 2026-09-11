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
async def test_device_key_can_post_status(api_client, root_admin, sample_truck):
    headers = auth_headers(root_admin)
    key_resp = await api_client.post(f"/api/trucks/{sample_truck.id}/rotate-key", headers=headers)
    device_key = key_resp.json()["device_api_key"]

    route_id = await _make_route(api_client, headers, str(sample_truck.id))

    resp = await api_client.post(
        f"/api/routes/{route_id}/status",
        json={
            "current_coordinates": {"lat": 19.0, "lon": -99.0},
            "current_speed": 70.0,
            "odometer": 500.0,
        },
        headers={"X-Device-Api-Key": device_key},
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_wrong_device_key_is_rejected(api_client, root_admin, sample_truck):
    headers = auth_headers(root_admin)
    await api_client.post(f"/api/trucks/{sample_truck.id}/rotate-key", headers=headers)
    route_id = await _make_route(api_client, headers, str(sample_truck.id))

    resp = await api_client.post(
        f"/api/routes/{route_id}/status",
        json={
            "current_coordinates": {"lat": 19.0, "lon": -99.0},
            "current_speed": 70.0,
            "odometer": 500.0,
        },
        headers={"X-Device-Api-Key": "not-the-real-key"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_no_credentials_is_rejected(api_client, root_admin, sample_truck):
    headers = auth_headers(root_admin)
    route_id = await _make_route(api_client, headers, str(sample_truck.id))

    resp = await api_client.post(
        f"/api/routes/{route_id}/status",
        json={
            "current_coordinates": {"lat": 19.0, "lon": -99.0},
            "current_speed": 70.0,
            "odometer": 500.0,
        },
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_truck_driver_jwt_cannot_post_status(api_client, root_admin, truck_driver_user, sample_truck):
    headers = auth_headers(root_admin)
    route_id = await _make_route(api_client, headers, str(sample_truck.id))

    resp = await api_client.post(
        f"/api/routes/{route_id}/status",
        json={
            "current_coordinates": {"lat": 19.0, "lon": -99.0},
            "current_speed": 70.0,
            "odometer": 500.0,
        },
        headers=auth_headers(truck_driver_user),
    )
    # truck_driver isn't in the allowed_roles for device-or-user ingestion (root_admin/guardian
    # only) -- see status_routes.py's post_status.
    assert resp.status_code == 401
