"""Route guardians (docs/designs/route-guardian-contracts.md): the guardian field and its
validation on routes (G1), guardian phone format (G2), the lifecycle guard (G3), the embedded
guardian block (G4), the env-file phone resolution on rotate-key (G5) and `/health`'s version
(G6)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.geo import GeoJSONPoint
from app.models.common import Role, RouteStatus
from app.models.route import Route
from app.models.truck import Truck
from app.models.user import User
from tests.conftest import auth_headers, make_guardian, make_rig, sha256_hex

GUARDIAN_PHONE_ERROR = "guardian phone_number must be in E.164 format, e.g. +523312345678"


async def _driver_id(api_client, headers) -> str:
    resp = await api_client.post(
        "/api/drivers",
        json={
            "first_name": "A",
            "last_name": "B",
            "license_number": "L-G1",
            "license_expiration": "2030-01-01",
            "phone_number": "+1-555-1111",
            "emergency_contact_name": "C",
            "emergency_contact_phone": "+1-555-2222",
            "blood_type": "O+",
        },
        headers=headers,
    )
    return resp.json()["id_driver"]


def _route_body(driver_id: str, truck_id: str, **overrides) -> dict:
    body = {
        "id_driver": driver_id,
        "id_truck": truck_id,
        "origin_name": "GDL",
        "destination_name": "CDMX",
        "destination_coordinates": {"lat": 19.43, "lon": -99.13},
        "estimated_departure": "2030-01-01T08:00:00Z",
        "operative_status": "scheduled",
    }
    body.update(overrides)
    return body


async def _create_route(api_client, root_admin, sample_truck, guardian: User, **overrides) -> dict:
    headers = auth_headers(root_admin)
    driver_id = await _driver_id(api_client, headers)
    resp = await api_client.post(
        "/api/routes",
        json=_route_body(driver_id, str(sample_truck.id), id_guardian=str(guardian.id), **overrides),
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# --- G1: create / update validation ----------------------------------------------------------


async def test_create_route_requires_id_guardian(api_client, root_admin, sample_truck):
    headers = auth_headers(root_admin)
    driver_id = await _driver_id(api_client, headers)
    resp = await api_client.post(
        "/api/routes", json=_route_body(driver_id, str(sample_truck.id)), headers=headers
    )
    assert resp.status_code == 422
    assert any(e["loc"][-1] == "id_guardian" for e in resp.json()["detail"])


@pytest.mark.parametrize(
    ("make", "detail"),
    [
        ("missing", "id_guardian not found"),
        ("malformed", "id_guardian not found"),
        ("wrong_role", "id_guardian must be an active guardian"),
        ("inactive", "id_guardian must be an active guardian"),
        ("bad_phone", "guardian has no valid E.164 phone"),
    ],
)
async def test_create_route_rejects_a_bad_guardian(api_client, root_admin, sample_truck, make, detail):
    if make == "missing":
        guardian_id = "64b0a00000000000000000aa"
    elif make == "malformed":
        guardian_id = "not-an-id"
    elif make == "wrong_role":
        guardian_id = str((await make_guardian(role=Role.TRUCK_DRIVER)).id)
    elif make == "inactive":
        guardian_id = str((await make_guardian(is_active=False)).id)
    else:
        guardian_id = str((await make_guardian(phone_number="555-0101")).id)
    headers = auth_headers(root_admin)
    driver_id = await _driver_id(api_client, headers)
    resp = await api_client.post(
        "/api/routes",
        json=_route_body(driver_id, str(sample_truck.id), id_guardian=guardian_id),
        headers=headers,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == detail


async def test_a_wrong_role_with_a_bad_phone_reports_the_role_first(api_client, root_admin, sample_truck):
    """Order: missing, then wrong role or inactive, then phone (G1)."""
    user = await make_guardian(role=Role.ADMIN, phone_number="n/a")
    headers = auth_headers(root_admin)
    driver_id = await _driver_id(api_client, headers)
    resp = await api_client.post(
        "/api/routes",
        json=_route_body(driver_id, str(sample_truck.id), id_guardian=str(user.id)),
        headers=headers,
    )
    assert resp.json()["detail"] == "id_guardian must be an active guardian"


async def test_guardian_phone_is_checked_after_normalisation(api_client, root_admin, sample_truck):
    guardian = await make_guardian(phone_number="+52 (33) 1234-5678")  # stored before G2
    route = await _create_route(api_client, root_admin, sample_truck, guardian)
    assert route["id_guardian"] == str(guardian.id)


async def test_route_out_embeds_the_guardian(api_client, root_admin, sample_truck):
    guardian = await make_guardian(first_name="Ana", last_name="López", phone_number="+523312345678")
    route = await _create_route(api_client, root_admin, sample_truck, guardian)
    expected = {
        "id_user": str(guardian.id),
        "first_name": "Ana",
        "last_name": "López",
        "phone_number": "+523312345678",
    }
    assert route["id_guardian"] == str(guardian.id)
    assert route["guardian"] == expected
    headers = auth_headers(root_admin)
    one = (await api_client.get(f"/api/routes/{route['id_route']}", headers=headers)).json()
    assert one["guardian"] == expected
    listed = (await api_client.get("/api/routes", headers=headers)).json()
    assert [r["guardian"] for r in listed] == [expected]


async def test_guardian_block_is_null_when_the_user_is_gone(api_client, root_admin, sample_truck):
    guardian = await make_guardian()
    route = await _create_route(api_client, root_admin, sample_truck, guardian)
    await guardian.delete()
    one = (await api_client.get(f"/api/routes/{route['id_route']}", headers=auth_headers(root_admin))).json()
    assert one["id_guardian"] == str(guardian.id)
    assert one["guardian"] is None


async def test_route_without_a_guardian_still_loads(api_client, root_admin, rig_a):
    """Routes from before guardians keep working (null guardian, G1)."""
    one = (await api_client.get(f"/api/routes/{rig_a.route_id}", headers=auth_headers(root_admin))).json()
    assert one["id_guardian"] is None and one["guardian"] is None


async def test_guardian_reads_the_guardian_block(api_client, root_admin, sample_truck, guardian):
    """Resolved server-side, so a guardian (no /api/users access) sees it too (G4)."""
    other = await make_guardian(first_name="Otra")
    route = await _create_route(api_client, root_admin, sample_truck, other)
    one = (await api_client.get(f"/api/routes/{route['id_route']}", headers=auth_headers(guardian))).json()
    assert one["guardian"]["first_name"] == "Otra"


async def test_update_assigns_and_changes_the_guardian(api_client, root_admin, admin_user, rig_a):
    first, second = await make_guardian(first_name="Uno"), await make_guardian(first_name="Dos")
    url = f"/api/routes/{rig_a.route_id}"
    resp = await api_client.put(url, json={"id_guardian": str(first.id)}, headers=auth_headers(admin_user))
    assert resp.status_code == 200
    assert resp.json()["guardian"]["first_name"] == "Uno"
    resp = await api_client.put(url, json={"id_guardian": str(second.id)}, headers=auth_headers(root_admin))
    assert resp.json()["id_guardian"] == str(second.id)
    # An explicit null is ignored, the route keeps its guardian.
    resp = await api_client.put(url, json={"id_guardian": None}, headers=auth_headers(root_admin))
    assert resp.status_code == 200 and resp.json()["id_guardian"] == str(second.id)
    assert (await Route.get(rig_a.route_id)).id_guardian == str(second.id)


async def test_assigning_a_guardian_does_not_touch_the_route_times(api_client, root_admin, mongo_client):
    """Only a status change sets `actual_departure`; a guardian-only update on a legacy
    in-progress route without one leaves it empty."""
    rig = await make_rig("TIMES-G")
    await (await Route.get(rig.route_id)).set({"actual_departure": None})
    guardian = await make_guardian()
    resp = await api_client.put(
        f"/api/routes/{rig.route_id}", json={"id_guardian": str(guardian.id)}, headers=auth_headers(root_admin)
    )
    assert resp.status_code == 200
    assert resp.json()["actual_departure"] is None


async def test_update_rejects_a_bad_guardian(api_client, root_admin, rig_a):
    inactive = await make_guardian(is_active=False)
    resp = await api_client.put(
        f"/api/routes/{rig_a.route_id}", json={"id_guardian": str(inactive.id)}, headers=auth_headers(root_admin)
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "id_guardian must be an active guardian"


@pytest.mark.parametrize("finished", [RouteStatus.COMPLETED, RouteStatus.CANCELLED])
async def test_update_guardian_on_a_finished_route_is_409(api_client, root_admin, mongo_client, finished):
    rig = await make_rig("FIN-G", route_status=finished)
    guardian = await make_guardian()
    resp = await api_client.put(
        f"/api/routes/{rig.route_id}", json={"id_guardian": str(guardian.id)}, headers=auth_headers(root_admin)
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "route is finished"
    # Its other fields stay editable.
    resp = await api_client.put(
        f"/api/routes/{rig.route_id}", json={"origin_name": "MTY"}, headers=auth_headers(root_admin)
    )
    assert resp.status_code == 200


async def test_only_admins_set_the_guardian(api_client, guardian, rig_a):
    other = await make_guardian()
    resp = await api_client.put(
        f"/api/routes/{rig_a.route_id}", json={"id_guardian": str(other.id)}, headers=auth_headers(guardian)
    )
    assert resp.status_code == 403


# --- G4: live, active, device views -----------------------------------------------------------


async def test_live_and_active_include_the_guardian(api_client, root_admin, mongo_client):
    g1, g2 = await make_guardian(first_name="Uno"), await make_guardian(first_name="Dos")
    a = await make_rig("LIVE-G1", guardian=g1)
    b = await make_rig("LIVE-G2", guardian=g2)
    c = await make_rig("LIVE-G3")
    headers = auth_headers(root_admin)
    live = (await api_client.get("/api/live", headers=headers)).json()
    by_id = {r["id_route"]: r for r in live["routes"]}
    assert by_id[a.route_id]["guardian"]["first_name"] == "Uno"
    assert by_id[b.route_id]["guardian"]["first_name"] == "Dos"
    assert by_id[c.route_id]["guardian"] is None
    one = (await api_client.get(f"/api/live?route={a.route_id}", headers=headers)).json()
    assert one["routes"][0]["guardian"]["id_user"] == str(g1.id)
    active = {r["id_route"]: r for r in (await api_client.get("/api/routes/active", headers=headers)).json()}
    assert active[b.route_id]["guardian"]["first_name"] == "Dos"
    assert active[b.route_id]["id_guardian"] == str(g2.id)


async def test_live_loads_guardians_in_one_query(api_client, root_admin, mongo_client, monkeypatch):
    for i in range(3):
        await make_rig(f"LIVE-Q{i}", guardian=await make_guardian())
    calls = []
    original = User.find

    def counting_find(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(User, "find", counting_find)
    resp = await api_client.get("/api/live", headers=auth_headers(root_admin))
    assert resp.status_code == 200
    assert len(calls) == 1


async def test_device_active_route_includes_the_guardian(api_client, mongo_client):
    guardian = await make_guardian(first_name="Dev")
    rig = await make_rig("DEV-G", guardian=guardian)
    resp = await api_client.get("/api/devices/me/active-route", headers=rig.headers)
    assert resp.status_code == 200
    assert resp.json()["guardian"]["first_name"] == "Dev"


# --- G2: guardian phone format ------------------------------------------------------------------


def _user_body(**overrides) -> dict:
    body = {
        "email": "new-guardian@example.com",
        "password": sha256_hex("pw123456"),
        "role": "guardian",
        "first_name": "New",
        "last_name": "Guardian",
        "phone_number": "+523312345678",
    }
    body.update(overrides)
    return body


@pytest.mark.parametrize("actor", ["root_admin", "admin_user"])
async def test_create_guardian_rejects_a_non_e164_phone(api_client, root_admin, admin_user, actor):
    user = {"root_admin": root_admin, "admin_user": admin_user}[actor]
    resp = await api_client.post(
        "/api/users", json=_user_body(phone_number="+52-555-01"), headers=auth_headers(user)
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == GUARDIAN_PHONE_ERROR


async def test_create_guardian_stores_the_phone_normalised(api_client, root_admin):
    resp = await api_client.post(
        "/api/users", json=_user_body(phone_number=" +52 (33) 1234-5678 "), headers=auth_headers(root_admin)
    )
    assert resp.status_code == 201
    assert resp.json()["phone_number"] == "+523312345678"
    stored = await User.find_one(User.email == "new-guardian@example.com")
    assert stored.phone_number == "+523312345678"


async def test_other_roles_keep_free_text_phones(api_client, root_admin):
    resp = await api_client.post(
        "/api/users",
        json=_user_body(role="truck_driver", phone_number="ext. 12 (desk)"),
        headers=auth_headers(root_admin),
    )
    assert resp.status_code == 201
    assert resp.json()["phone_number"] == "ext. 12 (desk)"


async def test_update_guardian_phone_is_checked_and_normalised(api_client, root_admin, guardian):
    url = f"/api/users/{guardian.id}"
    headers = auth_headers(root_admin)
    resp = await api_client.put(url, json={"phone_number": "5550101"}, headers=headers)
    assert resp.status_code == 422 and resp.json()["detail"] == GUARDIAN_PHONE_ERROR
    resp = await api_client.put(url, json={"phone_number": "+52 33 1234 5678"}, headers=headers)
    assert resp.status_code == 200 and resp.json()["phone_number"] == "+523312345678"


async def test_promoting_to_guardian_checks_the_stored_phone(api_client, root_admin, truck_driver_user):
    truck_driver_user.phone_number = "desk phone"
    await truck_driver_user.save()
    url = f"/api/users/{truck_driver_user.id}"
    headers = auth_headers(root_admin)
    resp = await api_client.put(url, json={"role": "guardian"}, headers=headers)
    assert resp.status_code == 422 and resp.json()["detail"] == GUARDIAN_PHONE_ERROR
    resp = await api_client.put(
        url, json={"role": "guardian", "phone_number": "+52-33-1234-5678"}, headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "guardian" and resp.json()["phone_number"] == "+523312345678"


async def test_untouched_bad_phone_never_fails_an_update(api_client, root_admin):
    old = await make_guardian(phone_number="old-format 555")
    resp = await api_client.put(
        f"/api/users/{old.id}", json={"first_name": "Renamed"}, headers=auth_headers(root_admin)
    )
    assert resp.status_code == 200
    assert resp.json()["phone_number"] == "old-format 555"


async def test_demoting_a_guardian_frees_the_phone_rule(api_client, root_admin):
    user = await make_guardian()
    resp = await api_client.put(
        f"/api/users/{user.id}", json={"role": "truck_driver", "phone_number": "n/a"}, headers=auth_headers(root_admin)
    )
    assert resp.status_code == 200 and resp.json()["phone_number"] == "n/a"


async def test_me_guardian_phone_rule(api_client, guardian, truck_driver_user):
    resp = await api_client.put("/api/auth/me", json={"phone_number": "555"}, headers=auth_headers(guardian))
    assert resp.status_code == 422 and resp.json()["detail"] == GUARDIAN_PHONE_ERROR
    resp = await api_client.put(
        "/api/auth/me", json={"phone_number": "+1 (555) 000-0009"}, headers=auth_headers(guardian)
    )
    assert resp.status_code == 200 and resp.json()["phone_number"] == "+15550000009"
    # A guardian with an old bad phone can still edit other fields.
    guardian.phone_number = "old"
    await guardian.save()
    resp = await api_client.put("/api/auth/me", json={"first_name": "G"}, headers=auth_headers(guardian))
    assert resp.status_code == 200
    # Other roles: free text.
    resp = await api_client.put(
        "/api/auth/me", json={"phone_number": "call the depot"}, headers=auth_headers(truck_driver_user)
    )
    assert resp.status_code == 200 and resp.json()["phone_number"] == "call the depot"


# --- G3: lifecycle guard ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status", [RouteStatus.SCHEDULED, RouteStatus.IN_PROGRESS, RouteStatus.IN_PROGRESS_ALERT]
)
@pytest.mark.parametrize(
    "action",
    [
        ("put", {"is_active": False}),
        ("put", {"role": "truck_driver"}),
        ("delete", None),
    ],
)
async def test_guardian_of_an_active_route_is_protected(api_client, root_admin, mongo_client, status, action):
    guardian = await make_guardian()
    await make_rig("G3-A", route_status=status, guardian=guardian)
    url = f"/api/users/{guardian.id}"
    method, body = action
    headers = auth_headers(root_admin)
    if method == "put":
        resp = await api_client.put(url, json=body, headers=headers)
    else:
        resp = await api_client.delete(url, headers=headers)
    assert resp.status_code == 409
    assert resp.json()["detail"] == "guardian is assigned to 1 active route(s); reassign them first"
    assert (await User.get(guardian.id)).is_active


async def test_guard_counts_every_active_route_and_admin_is_guarded_too(api_client, admin_user, mongo_client):
    guardian = await make_guardian()
    await make_rig("G3-B1", route_status=RouteStatus.SCHEDULED, guardian=guardian)
    await make_rig("G3-B2", route_status=RouteStatus.IN_PROGRESS, guardian=guardian)
    await make_rig("G3-B3", route_status=RouteStatus.COMPLETED, guardian=guardian)
    resp = await api_client.put(
        f"/api/users/{guardian.id}", json={"is_active": False}, headers=auth_headers(admin_user)
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "guardian is assigned to 2 active route(s); reassign them first"


async def test_guardian_of_only_finished_routes_can_be_changed(api_client, root_admin, mongo_client):
    guardian = await make_guardian()
    await make_rig("G3-C1", route_status=RouteStatus.COMPLETED, guardian=guardian)
    await make_rig("G3-C2", route_status=RouteStatus.CANCELLED, guardian=guardian)
    headers = auth_headers(root_admin)
    url = f"/api/users/{guardian.id}"
    assert (await api_client.put(url, json={"is_active": False}, headers=headers)).status_code == 200
    assert (await api_client.delete(url, headers=headers)).status_code == 200


async def test_other_updates_of_an_assigned_guardian_pass(api_client, root_admin, mongo_client):
    guardian = await make_guardian()
    await make_rig("G3-D", guardian=guardian)
    resp = await api_client.put(
        f"/api/users/{guardian.id}",
        json={"first_name": "Still", "is_active": True, "role": "guardian"},
        headers=auth_headers(root_admin),
    )
    assert resp.status_code == 200


# --- G5: rotate-key phone resolution ----------------------------------------------------------------


async def _route(truck_id: str, status: RouteStatus, guardian: User | None, departure_in_h: float = 1) -> Route:
    now = datetime.now(timezone.utc)
    route = Route(
        id_driver="64b0a00000000000000000d1",
        id_truck=truck_id,
        origin_name="GDL",
        destination_name="CDMX",
        destination_coordinates=GeoJSONPoint(coordinates=(-99.13, 19.43)),
        estimated_departure=now + timedelta(hours=departure_in_h),
        actual_departure=now - timedelta(hours=1) if status != RouteStatus.SCHEDULED else None,
        operative_status=status,
        id_guardian=str(guardian.id) if guardian is not None else None,
    )
    await route.insert()
    return route


async def _rotate(api_client, user, truck, body=...):
    url = f"/api/trucks/{truck.id}/rotate-key"
    if body is ...:
        return await api_client.post(url, headers=auth_headers(user))
    return await api_client.post(url, json=body, headers=auth_headers(user))


async def test_rotate_key_without_body_uses_the_in_progress_route(api_client, root_admin, sample_truck):
    guardian = await make_guardian(first_name="Ana", last_name="López", phone_number="+52 33 1234 5678")
    route = await _route(str(sample_truck.id), RouteStatus.IN_PROGRESS, guardian)
    resp = await _rotate(api_client, root_admin, sample_truck)
    assert resp.status_code == 200
    body = resp.json()
    assert body["id_truck"] == str(sample_truck.id) and body["device_api_key"]
    assert body["alert_phone"] == "+523312345678"
    assert body["alert_phone_source"] == "route_guardian"
    assert body["id_route"] == str(route.id)
    assert body["guardian_name"] == "Ana López"


async def test_rotate_key_with_empty_body_behaves_like_none(api_client, admin_user, sample_truck):
    guardian = await make_guardian()
    route = await _route(str(sample_truck.id), RouteStatus.IN_PROGRESS_ALERT, guardian)
    body = (await _rotate(api_client, admin_user, sample_truck, {})).json()
    assert body["alert_phone_source"] == "route_guardian" and body["id_route"] == str(route.id)


async def test_rotate_key_given_route_wins(api_client, root_admin, sample_truck):
    g_live, g_given = await make_guardian(first_name="Live"), await make_guardian(first_name="Given", phone_number="+525550100105")
    await _route(str(sample_truck.id), RouteStatus.IN_PROGRESS, g_live)
    given = await _route(str(sample_truck.id), RouteStatus.SCHEDULED, g_given, departure_in_h=48)
    body = (await _rotate(api_client, root_admin, sample_truck, {"id_route": str(given.id)})).json()
    assert body["alert_phone"] == "+525550100105"
    assert body["id_route"] == str(given.id)
    assert body["guardian_name"].startswith("Given")


async def test_rotate_key_unusable_given_guardian_falls_back_to_in_progress(api_client, root_admin, sample_truck):
    live_guardian = await make_guardian(first_name="Live")
    live = await _route(str(sample_truck.id), RouteStatus.IN_PROGRESS, live_guardian)
    inactive = await make_guardian(is_active=False)
    given = await _route(str(sample_truck.id), RouteStatus.SCHEDULED, inactive)
    body = (await _rotate(api_client, root_admin, sample_truck, {"id_route": str(given.id)})).json()
    assert body["alert_phone_source"] == "route_guardian"
    assert body["id_route"] == str(live.id)


async def test_rotate_key_falls_back_to_the_earliest_scheduled_route(api_client, root_admin, sample_truck):
    await _route(str(sample_truck.id), RouteStatus.IN_PROGRESS, await make_guardian(phone_number="bad"))
    later = await _route(str(sample_truck.id), RouteStatus.SCHEDULED, await make_guardian(first_name="Later"), 30)
    earliest = await _route(str(sample_truck.id), RouteStatus.SCHEDULED, await make_guardian(first_name="Early"), 5)
    await _route(str(sample_truck.id), RouteStatus.COMPLETED, await make_guardian(first_name="Done"))
    body = (await _rotate(api_client, root_admin, sample_truck)).json()
    assert body["id_route"] == str(earliest.id) != str(later.id)
    assert body["guardian_name"].startswith("Early")


async def test_rotate_key_earliest_scheduled_without_usable_guardian_is_not_skipped(
    api_client, root_admin, sample_truck
):
    """Rule 3 is the *earliest* scheduled route: if its guardian isn't usable, a later scheduled
    route isn't tried; the truck fallback applies."""
    await _route(str(sample_truck.id), RouteStatus.SCHEDULED, await make_guardian(), 30)
    earliest = await _route(str(sample_truck.id), RouteStatus.SCHEDULED, None, 5)
    sample_truck.alert_phone = "+523300000000"
    await sample_truck.save()
    body = (await _rotate(api_client, root_admin, sample_truck)).json()
    assert body["alert_phone_source"] == "truck"
    assert body["id_route"] == str(earliest.id)


async def test_rotate_key_truck_fallback_names_the_route_considered(api_client, root_admin, sample_truck):
    sample_truck.alert_phone = "+523300000000"
    await sample_truck.save()
    given = await _route(str(sample_truck.id), RouteStatus.SCHEDULED, None)
    live = await _route(str(sample_truck.id), RouteStatus.IN_PROGRESS, None)
    body = (await _rotate(api_client, root_admin, sample_truck, {"id_route": str(given.id)})).json()
    assert body == {
        **body,
        "alert_phone": "+523300000000",
        "alert_phone_source": "truck",
        "id_route": str(given.id),
        "guardian_name": None,
    }
    body = (await _rotate(api_client, root_admin, sample_truck)).json()
    assert body["alert_phone_source"] == "truck" and body["id_route"] == str(live.id)


async def test_rotate_key_truck_fallback_without_routes(api_client, root_admin, sample_truck):
    sample_truck.alert_phone = "+523300000000"
    await sample_truck.save()
    body = (await _rotate(api_client, root_admin, sample_truck)).json()
    assert (body["alert_phone"], body["alert_phone_source"], body["id_route"], body["guardian_name"]) == (
        "+523300000000", "truck", None, None,
    )


async def test_rotate_key_nothing_found_is_all_null(api_client, root_admin, sample_truck):
    await _route(str(sample_truck.id), RouteStatus.IN_PROGRESS, None)
    body = (await _rotate(api_client, root_admin, sample_truck)).json()
    assert body["device_api_key"]
    assert (body["alert_phone"], body["alert_phone_source"], body["id_route"], body["guardian_name"]) == (
        None, None, None, None,
    )


@pytest.mark.parametrize("which", ["unknown", "malformed", "other_truck"])
async def test_rotate_key_bad_id_route_is_422_and_keeps_the_old_key(
    api_client, root_admin, sample_truck, which
):
    first = (await _rotate(api_client, root_admin, sample_truck)).json()["device_api_key"]
    if which == "unknown":
        id_route = "64b0a00000000000000000ee"
    elif which == "malformed":
        id_route = "nope"
    else:
        other = Truck(plate_number="OTHER-1", brand="B", model="M", company_number="C-O")
        await other.insert()
        id_route = str((await _route(str(other.id), RouteStatus.IN_PROGRESS, await make_guardian())).id)
    resp = await _rotate(api_client, root_admin, sample_truck, {"id_route": id_route})
    assert resp.status_code == 422
    assert resp.json()["detail"] == "id_route not found for this truck"
    # The old key still authenticates: the 422 came before the rotation.
    still = await api_client.get("/api/devices/me/active-route", headers={"X-Device-Api-Key": first})
    assert still.status_code != 401


# --- G6: /health version ------------------------------------------------------------------------------


async def test_health_reports_the_version(api_client, monkeypatch):
    body = (await api_client.get("/health")).json()
    assert body == {"status": "ok", "version": settings.argus_version}
    monkeypatch.setattr(settings, "argus_version", "abc1234")
    assert (await api_client.get("/health")).json()["version"] == "abc1234"


def test_version_defaults_to_dev(monkeypatch):
    from app.config import Settings

    monkeypatch.delenv("ARGUS_VERSION", raising=False)
    assert Settings(_env_file=None).argus_version == "dev"
    monkeypatch.setenv("ARGUS_VERSION", "9c4d479")
    assert Settings(_env_file=None).argus_version == "9c4d479"
