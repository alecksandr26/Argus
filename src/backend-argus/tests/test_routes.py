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
            "vigilance": "low",
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


# --- /api/routes/active corner cases ---------------------------------------------------------

from app.models.common import RouteStatus  # noqa: E402
from tests.conftest import make_rig, status_body  # noqa: E402


async def test_active_routes_lists_route_with_no_status_yet(api_client, root_admin, rig_a):
    resp = await api_client.get("/api/routes/active", headers=auth_headers(root_admin))
    [route] = [r for r in resp.json() if r["id_route"] == rig_a.route_id]
    assert route.get("latest_status") is None


async def test_active_routes_excludes_completed_and_cancelled(api_client, root_admin, mongo_client):
    done = await make_rig("ACT-DONE", route_status=RouteStatus.COMPLETED)
    cancelled = await make_rig("ACT-CANC", route_status=RouteStatus.CANCELLED)
    live = await make_rig("ACT-LIVE", route_status=RouteStatus.IN_PROGRESS_ALERT)
    ids = [r["id_route"] for r in (await api_client.get(
        "/api/routes/active", headers=auth_headers(root_admin))).json()]
    assert live.route_id in ids
    assert done.route_id not in ids and cancelled.route_id not in ids


async def test_active_routes_embeds_the_newest_of_several_statuses(api_client, root_admin, rig_a):
    url = f"/api/routes/{rig_a.route_id}/status"
    for vigilance in ("low", "medium", "critical"):
        await api_client.post(url, json=status_body(vigilance=vigilance), headers=rig_a.headers)
    resp = await api_client.get("/api/routes/active", headers=auth_headers(root_admin))
    [route] = [r for r in resp.json() if r["id_route"] == rig_a.route_id]
    assert route["latest_status"]["vigilance"] == "critical"
