"""The legacy device "resolve" (now: driver OK), reviewing and de-escalating alerts.

The SIM800L's HTTP stack has no PUT, so the device needs a POST to resolve (BE-09). A device
resolve now only sets `driver_ok_at`; closing is `POST /api/incidents/{id}/close` by a guardian
or root_admin (docs/designs/final-tweaks-contracts.md, C2.4). See
docs/designs/edge-cloud-scenarios.md for the scenario IDs.
"""
from __future__ import annotations

import pytest

from app.geo import GeoJSONPoint
from app.models.alert import Alert
from app.models.common import AlertSource, RouteStatus, Severity
from app.models.route import Route
from tests.conftest import auth_headers, fusion_alert_body, make_rig


async def _alert(api_client, rig, **overrides) -> dict:
    resp = await api_client.post(
        "/api/alerts", json=fusion_alert_body(rig.route_id, **overrides), headers=rig.headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _get(api_client, user, alert_id) -> dict:
    return (await api_client.get(f"/api/alerts/{alert_id}", headers=auth_headers(user))).json()


async def _finish(route_id: str, state: RouteStatus) -> None:
    route = await Route.get(route_id)
    await route.set({"operative_status": state})


async def test_post_resolve_by_device_key_sets_driver_ok_at(api_client, root_admin, rig_a):
    alert = await _alert(api_client, rig_a)
    resp = await api_client.post(
        f"/api/alerts/{alert['id_alert']}/resolve",
        json={"resolved_at": "2030-01-01T09:00:00Z"},
        headers=rig_a.headers,
    )
    assert resp.status_code == 200
    after = await _get(api_client, root_admin, alert["id_alert"])
    assert after["driver_ok_at"] is not None
    assert "resolved_at" not in after


async def test_device_resolve_never_closes_the_alert_or_its_incident(api_client, root_admin, rig_a):
    alert = await _alert(api_client, rig_a)
    await api_client.post(
        f"/api/alerts/{alert['id_alert']}/resolve",
        json={"resolved_at": "2030-01-01T09:00:00Z"},
        headers=rig_a.headers,
    )
    after = await _get(api_client, root_admin, alert["id_alert"])
    incident = (
        await api_client.get(
            f"/api/incidents/{after['id_incident']}", headers=auth_headers(root_admin)
        )
    ).json()
    assert after["closed_at"] is None
    assert incident["closed_at"] is None
    assert incident["driver_ok_at"] is not None


@pytest.mark.parametrize("finished", [RouteStatus.COMPLETED, RouteStatus.CANCELLED])
async def test_device_post_resolve_on_finished_route_is_409(api_client, rig_a, finished):
    alert = await _alert(api_client, rig_a)
    await _finish(rig_a.route_id, finished)
    resp = await api_client.post(
        f"/api/alerts/{alert['id_alert']}/resolve",
        json={"resolved_at": "2030-01-01T09:00:00Z"},
        headers=rig_a.headers,
    )
    assert resp.status_code == 409


async def test_device_put_resolved_at_on_finished_route_is_409(api_client, root_admin, rig_a):
    alert = await _alert(api_client, rig_a)
    await _finish(rig_a.route_id, RouteStatus.CANCELLED)
    resp = await api_client.put(
        f"/api/alerts/{alert['id_alert']}",
        json={"resolved_at": "2030-01-01T09:00:00Z"},
        headers=rig_a.headers,
    )
    assert resp.status_code == 409
    assert (await _get(api_client, root_admin, alert["id_alert"]))["driver_ok_at"] is None


async def test_guardian_review_on_finished_route_is_allowed(api_client, guardian, rig_a):
    alert = await _alert(api_client, rig_a)
    await _finish(rig_a.route_id, RouteStatus.COMPLETED)
    resp = await api_client.put(
        f"/api/alerts/{alert['id_alert']}",
        json={"operator_notes": "called the driver"},
        headers=auth_headers(guardian),
    )
    assert resp.status_code == 200
    assert resp.json()["operator_notes"] == "called the driver"


RETIRED = "resolved_at is retired; close the incident with POST /api/incidents/{id}/close"


@pytest.mark.parametrize("role", ["guardian", "root_admin"])
async def test_user_put_resolved_at_is_422(api_client, guardian, root_admin, rig_a, role):
    """C2.4 [sync 2]: a human closes the incident instead; the alert is left untouched."""
    user = {"guardian": guardian, "root_admin": root_admin}[role]
    alert = await _alert(api_client, rig_a)
    resp = await api_client.put(
        f"/api/alerts/{alert['id_alert']}",
        json={"resolved_at": "2030-01-01T09:00:00Z", "operator_notes": "ignored too"},
        headers=auth_headers(user),
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == RETIRED
    after = await _get(api_client, user, alert["id_alert"])
    assert after["driver_ok_at"] is None
    assert after["operator_notes"] == ""


@pytest.mark.parametrize("role", ["guardian", "root_admin"])
async def test_user_post_resolve_is_422(api_client, guardian, root_admin, rig_a, role):
    user = {"guardian": guardian, "root_admin": root_admin}[role]
    alert = await _alert(api_client, rig_a)
    resp = await api_client.post(
        f"/api/alerts/{alert['id_alert']}/resolve",
        json={"resolved_at": "2030-01-01T09:00:00Z"},
        headers=auth_headers(user),
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == RETIRED
    assert (await _get(api_client, user, alert["id_alert"]))["driver_ok_at"] is None


async def test_other_roles_resolving_stay_403(api_client, admin_user, rig_a):
    """Roles that could never touch an alert keep their 403 (authorization comes first)."""
    alert = await _alert(api_client, rig_a)
    put = await api_client.put(
        f"/api/alerts/{alert['id_alert']}",
        json={"resolved_at": "2030-01-01T09:00:00Z"},
        headers=auth_headers(admin_user),
    )
    post = await api_client.post(
        f"/api/alerts/{alert['id_alert']}/resolve",
        json={"resolved_at": "2030-01-01T09:00:00Z"},
        headers=auth_headers(admin_user),
    )
    assert (put.status_code, post.status_code) == (403, 403)


async def test_guardian_review_without_resolved_at_still_works(api_client, guardian, rig_a):
    alert = await _alert(api_client, rig_a)
    resp = await api_client.put(
        f"/api/alerts/{alert['id_alert']}",
        json={"reviewed_by_operator": True, "operator_notes": "checked"},
        headers=auth_headers(guardian),
    )
    assert resp.status_code == 200
    assert resp.json()["reviewed_by_operator"] is True


async def test_post_resolve_with_other_trucks_key_is_401(api_client, rig_a, rig_b):
    alert = await _alert(api_client, rig_a)
    resp = await api_client.post(
        f"/api/alerts/{alert['id_alert']}/resolve",
        json={"resolved_at": "2030-01-01T09:00:00Z"},
        headers=rig_b.headers,
    )
    assert resp.status_code == 401


async def test_post_resolve_unknown_alert_is_404(api_client, rig_a):
    resp = await api_client.post(
        "/api/alerts/64b0a00000000000000000aa/resolve",
        json={"resolved_at": "2030-01-01T09:00:00Z"},
        headers=rig_a.headers,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Alert not found"  # not a missing endpoint


async def test_resolve_twice_keeps_first_driver_ok_at(api_client, root_admin, rig_a):
    alert = await _alert(api_client, rig_a)
    for when in ("2030-01-01T09:00:00Z", "2030-01-01T10:00:00Z"):
        resp = await api_client.put(
            f"/api/alerts/{alert['id_alert']}", json={"resolved_at": when}, headers=rig_a.headers
        )
        assert resp.status_code == 200
    driver_ok_at = (await _get(api_client, root_admin, alert["id_alert"]))["driver_ok_at"]
    assert driver_ok_at.startswith("2030-01-01T09:00:00")


async def test_resolved_at_before_alert_timestamp_is_422(api_client, rig_a):
    alert = await _alert(api_client, rig_a)
    resp = await api_client.put(
        f"/api/alerts/{alert['id_alert']}",
        json={"resolved_at": "2001-01-01T00:00:00Z"},
        headers=rig_a.headers,
    )
    assert resp.status_code == 422


async def test_naive_resolved_at_is_422(api_client, rig_a):
    alert = await _alert(api_client, rig_a)
    resp = await api_client.put(
        f"/api/alerts/{alert['id_alert']}",
        json={"resolved_at": "2030-01-01T09:00:00"},
        headers=rig_a.headers,
    )
    assert resp.status_code == 422


async def test_resolving_escalation_marks_linked_original(api_client, root_admin, rig_a):
    original = await _alert(api_client, rig_a, severity_level="medium", grip_status="good")
    escalation = await _alert(api_client, rig_a, related_alert_id=original["id_alert"])
    await api_client.put(
        f"/api/alerts/{escalation['id_alert']}",
        json={"resolved_at": "2030-01-01T09:00:00Z"},
        headers=rig_a.headers,
    )
    assert (await _get(api_client, root_admin, original["id_alert"]))["driver_ok_at"] is not None


async def test_device_cannot_set_review_fields(api_client, rig_a):
    alert = await _alert(api_client, rig_a)
    resp = await api_client.put(
        f"/api/alerts/{alert['id_alert']}",
        json={"reviewed_by_operator": True},
        headers=rig_a.headers,
    )
    assert resp.status_code == 403


async def test_deescalate_then_resolve(api_client, guardian, root_admin, rig_a):
    alert = await _alert(api_client, rig_a)
    de = await api_client.post(
        f"/api/alerts/{alert['id_alert']}/de-escalate",
        json={"severity_level": "medium", "reason": "called the driver"},
        headers=auth_headers(guardian),
    )
    res = await api_client.put(
        f"/api/alerts/{alert['id_alert']}",
        json={"resolved_at": "2030-01-01T09:00:00Z"},
        headers=rig_a.headers,
    )
    after = await _get(api_client, root_admin, alert["id_alert"])
    assert (de.status_code, res.status_code) == (200, 200)
    assert after["severity_level"] == "medium"
    assert after["original_severity_level"] == "critical"
    assert after["driver_ok_at"] is not None


async def test_deescalate_recomputes_the_incident_severity(api_client, guardian, root_admin, rig_a):
    alert = await _alert(api_client, rig_a)
    await api_client.post(
        f"/api/alerts/{alert['id_alert']}/de-escalate",
        json={"severity_level": "medium", "reason": "called the driver"},
        headers=auth_headers(guardian),
    )
    incident = (
        await api_client.get(
            f"/api/incidents/{alert['id_incident']}", headers=auth_headers(root_admin)
        )
    ).json()
    assert incident["severity"] == "medium"


async def test_resolve_then_deescalate_keeps_driver_ok(api_client, guardian, root_admin, rig_a):
    alert = await _alert(api_client, rig_a)
    await api_client.put(
        f"/api/alerts/{alert['id_alert']}",
        json={"resolved_at": "2030-01-01T09:00:00Z"},
        headers=rig_a.headers,
    )
    de = await api_client.post(
        f"/api/alerts/{alert['id_alert']}/de-escalate",
        json={"severity_level": "low", "reason": "false positive"},
        headers=auth_headers(guardian),
    )
    after = await _get(api_client, root_admin, alert["id_alert"])
    assert de.status_code == 200
    assert after["driver_ok_at"] is not None


async def test_old_panic_alert_is_still_readable(api_client, root_admin, rig_a):
    """The panic button is retired: creating a panic alert is a 422 (test_alerts.py), but rows
    stored before stay readable."""
    stored = Alert(
        id_route=rig_a.route_id,
        alert_type="Panic button pressed",
        severity_level=Severity.CRITICAL,
        source=AlertSource.PANIC_BUTTON,
        coordinates=GeoJSONPoint(coordinates=(-103.3, 20.6)),
        speed_at_event=80.0,
    )
    await stored.insert()
    listed = await api_client.get(
        "/api/alerts", params={"source": "panic_button"}, headers=auth_headers(root_admin)
    )
    one = await _get(api_client, root_admin, str(stored.id))
    assert [a["id_alert"] for a in listed.json()] == [str(stored.id)]
    assert one["source"] == "panic_button"
    assert one["id_incident"] is None


async def test_legacy_low_alert_gets_no_incident(api_client, root_admin, mongo_client):
    rig = await make_rig("LOW-1")
    alert = await _alert(api_client, rig, severity_level="low")
    route = (await api_client.get(f"/api/routes/{rig.route_id}", headers=auth_headers(root_admin))).json()
    assert alert["id_incident"] is None
    assert route["incident_state"] == "fine"


async def test_incident_severity_never_drops_below_medium(api_client, guardian, root_admin, rig_a):
    """C3.4 [sync 2]: every alert de-escalated to low -> the incident stays open, `medium`."""
    first = await _alert(api_client, rig_a, severity_level="medium", grip_status="good")
    second = await _alert(api_client, rig_a, related_alert_id=first["id_alert"])
    assert first["id_incident"] == second["id_incident"]
    for alert in (first, second):
        resp = await api_client.post(
            f"/api/alerts/{alert['id_alert']}/de-escalate",
            json={"severity_level": "low", "reason": "false positive"},
            headers=auth_headers(guardian),
        )
        assert resp.status_code == 200
        assert resp.json()["severity_level"] == "low"
    incident = (
        await api_client.get(
            f"/api/incidents/{first['id_incident']}", headers=auth_headers(root_admin)
        )
    ).json()
    assert incident["severity"] == "medium"
    assert incident["closed_at"] is None
    assert incident["alert_count"] == 2
