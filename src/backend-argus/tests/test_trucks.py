import pytest

from tests.conftest import auth_headers


@pytest.mark.asyncio
async def test_create_get_update_delete_truck(api_client, root_admin):
    headers = auth_headers(root_admin)

    create_resp = await api_client.post(
        "/api/trucks",
        json={"plate_number": "ABC-123", "brand": "Volvo", "model": "FH", "company_number": "T-1"},
        headers=headers,
    )
    assert create_resp.status_code == 201
    truck_id = create_resp.json()["id_truck"]
    assert create_resp.json()["operative_status"] == "inactive"

    get_resp = await api_client.get(f"/api/trucks/{truck_id}", headers=headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["plate_number"] == "ABC-123"

    update_resp = await api_client.put(
        f"/api/trucks/{truck_id}", json={"operative_status": "active"}, headers=headers
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["operative_status"] == "active"

    delete_resp = await api_client.delete(f"/api/trucks/{truck_id}", headers=headers)
    assert delete_resp.status_code == 200

    missing_resp = await api_client.get(f"/api/trucks/{truck_id}", headers=headers)
    assert missing_resp.status_code == 404


@pytest.mark.asyncio
async def test_rotate_key_returns_key_once(api_client, root_admin, sample_truck):
    headers = auth_headers(root_admin)
    resp = await api_client.post(f"/api/trucks/{sample_truck.id}/rotate-key", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["id_truck"] == str(sample_truck.id)
    assert len(body["device_api_key"]) > 20

    # rotate-key itself isn't returned on later reads of the truck
    get_resp = await api_client.get(f"/api/trucks/{sample_truck.id}", headers=headers)
    assert "device_api_key" not in get_resp.json()
    assert "device_api_key_hash" not in get_resp.json()
