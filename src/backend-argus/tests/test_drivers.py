import pytest

from tests.conftest import auth_headers


@pytest.mark.asyncio
async def test_create_and_get_driver(api_client, root_admin):
    headers = auth_headers(root_admin)
    resp = await api_client.post(
        "/api/drivers",
        json={
            "first_name": "Jorge",
            "last_name": "Ramirez",
            "license_number": "LIC-1",
            "license_expiration": "2030-01-01",
            "phone_number": "+1-555-1111",
            "emergency_contact_name": "Marta",
            "emergency_contact_phone": "+1-555-2222",
            "blood_type": "O+",
        },
        headers=headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    # ER typo-fix: field is `blood_type`, not the diagram's `blod_type`.
    assert body["blood_type"] == "O+"

    get_resp = await api_client.get(f"/api/drivers/{body['id_driver']}", headers=headers)
    assert get_resp.status_code == 200
