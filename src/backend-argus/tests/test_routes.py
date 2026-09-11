import pytest

from tests.conftest import auth_headers


async def _make_driver(api_client, headers) -> str:
    resp = await api_client.post(
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
    return resp.json()["id_driver"]


@pytest.mark.asyncio
async def test_create_route_and_coordinates_round_trip(api_client, root_admin, sample_truck):
    headers = auth_headers(root_admin)
    driver_id = await _make_driver(api_client, headers)

    resp = await api_client.post(
        "/api/routes",
        json={
            "id_driver": driver_id,
            "id_truck": str(sample_truck.id),
            "origin_name": "CDMX",
            "destination_name": "GDL",
            "destination_coordinates": {"lat": 20.6597, "lon": -103.3496},
            "estimated_departure": "2030-01-01T08:00:00Z",
            "operative_status": "in_progress",
        },
        headers=headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    # {lat, lon} out, exactly as posted — not GeoJSON [lon, lat] — see app/geo.py.
    assert body["destination_coordinates"] == {"lat": 20.6597, "lon": -103.3496}
    return body


@pytest.mark.asyncio
async def test_active_routes_embeds_latest_status(api_client, root_admin, sample_truck):
    headers = auth_headers(root_admin)
    driver_id = await _make_driver(api_client, headers)

    route_resp = await api_client.post(
        "/api/routes",
        json={
            "id_driver": driver_id,
            "id_truck": str(sample_truck.id),
            "origin_name": "CDMX",
            "destination_name": "GDL",
            "destination_coordinates": {"lat": 20.0, "lon": -103.0},
            "estimated_departure": "2030-01-01T08:00:00Z",
            "operative_status": "in_progress",
        },
        headers=headers,
    )
    route_id = route_resp.json()["id_route"]

    await api_client.post(
        f"/api/routes/{route_id}/status",
        json={
            "current_coordinates": {"lat": 19.5, "lon": -99.5},
            "current_speed": 90.0,
            "odometer": 1000.0,
            "vigilance": "normal",
        },
        headers=headers,
    )

    active_resp = await api_client.get("/api/routes/active", headers=headers)
    assert active_resp.status_code == 200
    routes = active_resp.json()
    assert len(routes) == 1
    assert routes[0]["id_route"] == route_id
    assert routes[0]["latest_status"]["current_speed"] == 90.0
    assert routes[0]["truck_plate_number"] == sample_truck.plate_number


@pytest.mark.asyncio
async def test_active_endpoint_excludes_scheduled_routes(api_client, root_admin, sample_truck):
    headers = auth_headers(root_admin)
    driver_id = await _make_driver(api_client, headers)

    await api_client.post(
        "/api/routes",
        json={
            "id_driver": driver_id,
            "id_truck": str(sample_truck.id),
            "origin_name": "CDMX",
            "destination_name": "GDL",
            "destination_coordinates": {"lat": 20.0, "lon": -103.0},
            "estimated_departure": "2030-01-01T08:00:00Z",
            "operative_status": "scheduled",
        },
        headers=headers,
    )

    active_resp = await api_client.get("/api/routes/active", headers=headers)
    assert active_resp.json() == []
