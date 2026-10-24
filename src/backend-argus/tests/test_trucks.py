import pytest

from app.models.truck import Truck
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


# --- SMS alert phone (docs/designs/grip-alarm-contracts.md, C1.1) ------------------------------

TRUCK_BODY = {"plate_number": "SMS-001", "brand": "Volvo", "model": "FH", "company_number": "T-9"}


@pytest.mark.asyncio
async def test_alert_phone_defaults_to_null_in_every_response(api_client, root_admin, sample_truck):
    headers = auth_headers(root_admin)
    create_resp = await api_client.post("/api/trucks", json=TRUCK_BODY, headers=headers)
    assert create_resp.status_code == 201
    assert create_resp.json()["alert_phone"] is None

    get_resp = await api_client.get(f"/api/trucks/{sample_truck.id}", headers=headers)
    assert get_resp.json()["alert_phone"] is None
    list_resp = await api_client.get("/api/trucks", headers=headers)
    assert [truck["alert_phone"] for truck in list_resp.json()] == [None, None]


@pytest.mark.asyncio
async def test_create_with_alert_phone_stores_and_returns_it(api_client, root_admin):
    headers = auth_headers(root_admin)
    resp = await api_client.post(
        "/api/trucks", json={**TRUCK_BODY, "alert_phone": "+523312345678"}, headers=headers
    )
    assert resp.status_code == 201
    assert resp.json()["alert_phone"] == "+523312345678"

    truck = await Truck.get(resp.json()["id_truck"])
    assert truck.alert_phone == "+523312345678"
    get_resp = await api_client.get(f"/api/trucks/{truck.id}", headers=headers)
    assert get_resp.json()["alert_phone"] == "+523312345678"


@pytest.mark.asyncio
async def test_create_with_empty_alert_phone_stores_none(api_client, root_admin):
    resp = await api_client.post(
        "/api/trucks", json={**TRUCK_BODY, "alert_phone": ""}, headers=auth_headers(root_admin)
    )
    assert resp.status_code == 201
    assert resp.json()["alert_phone"] is None
    assert (await Truck.get(resp.json()["id_truck"])).alert_phone is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sent", "stored"),
    [(" +523312345678\n", "+523312345678"), ("   ", None)],
    ids=["outer_whitespace_stripped", "whitespace_only_is_none"],
)
async def test_alert_phone_is_stripped_before_the_check(api_client, root_admin, sent, stored):
    resp = await api_client.post(
        "/api/trucks", json={**TRUCK_BODY, "alert_phone": sent}, headers=auth_headers(root_admin)
    )
    assert resp.status_code == 201
    assert resp.json()["alert_phone"] == stored
    assert (await Truck.get(resp.json()["id_truck"])).alert_phone == stored


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phone",
    [
        "523312345678",
        "+0523312345678",
        "+52 33 1234 5678",
        "+52-33-1234-5678",
        "+1234567",
        "+1234567890123456",
        "+52331234567a",
        "+",
        523312345678,
    ],
    ids=[
        "no_plus",
        "country_code_starts_with_0",
        "inner_spaces",
        "dashes",
        "7_digits",
        "16_digits",
        "letter",
        "plus_only",
        "not_a_string",
    ],
)
async def test_invalid_alert_phone_is_422(api_client, root_admin, sample_truck, phone):
    headers = auth_headers(root_admin)
    create_resp = await api_client.post(
        "/api/trucks", json={**TRUCK_BODY, "alert_phone": phone}, headers=headers
    )
    assert create_resp.status_code == 422
    update_resp = await api_client.put(
        f"/api/trucks/{sample_truck.id}", json={"alert_phone": phone}, headers=headers
    )
    assert update_resp.status_code == 422
    assert (await Truck.get(sample_truck.id)).alert_phone is None


@pytest.mark.asyncio
@pytest.mark.parametrize("phone", ["+12345678", "+123456789012345"], ids=["8_digits", "15_digits"])
async def test_alert_phone_length_bounds_are_accepted(api_client, root_admin, phone):
    resp = await api_client.post(
        "/api/trucks", json={**TRUCK_BODY, "alert_phone": phone}, headers=auth_headers(root_admin)
    )
    assert resp.status_code == 201
    assert resp.json()["alert_phone"] == phone


@pytest.mark.asyncio
@pytest.mark.parametrize("cleared", [None, ""], ids=["null", "empty"])
async def test_update_sets_then_clears_alert_phone(api_client, root_admin, sample_truck, cleared):
    headers = auth_headers(root_admin)
    url = f"/api/trucks/{sample_truck.id}"

    set_resp = await api_client.put(url, json={"alert_phone": "+523312345678"}, headers=headers)
    assert set_resp.status_code == 200
    assert set_resp.json()["alert_phone"] == "+523312345678"

    # An update that doesn't mention the phone leaves it alone.
    other_resp = await api_client.put(url, json={"operative_status": "active"}, headers=headers)
    assert other_resp.json()["alert_phone"] == "+523312345678"

    clear_resp = await api_client.put(url, json={"alert_phone": cleared}, headers=headers)
    assert clear_resp.status_code == 200
    assert clear_resp.json()["alert_phone"] is None
    assert (await Truck.get(sample_truck.id)).alert_phone is None


@pytest.mark.asyncio
async def test_alert_phone_follows_the_truck_rbac(
    api_client, admin_user, guardian, truck_driver_user, sample_truck
):
    url = f"/api/trucks/{sample_truck.id}"
    body = {"alert_phone": "+523312345678"}
    for user in (guardian, truck_driver_user):
        update = await api_client.put(url, json=body, headers=auth_headers(user))
        assert update.status_code == 403
        create = await api_client.post(
            "/api/trucks", json={**TRUCK_BODY, **body}, headers=auth_headers(user)
        )
        assert create.status_code == 403
    assert (await Truck.get(sample_truck.id)).alert_phone is None

    # admin owns the truck roster, so it may set the phone like any other truck field...
    resp = await api_client.put(url, json=body, headers=auth_headers(admin_user))
    assert resp.status_code == 200
    assert resp.json()["alert_phone"] == "+523312345678"
    # ...and every signed-in role reads it, like the rest of the truck.
    read = await api_client.get(url, headers=auth_headers(truck_driver_user))
    assert read.json()["alert_phone"] == "+523312345678"
