"""Resolving, reviewing and de-escalating alerts, from the device and from humans.

The SIM800L's HTTP stack has no PUT, so the device needs a POST to resolve (BE-09). See
docs/designs/edge-cloud-scenarios.md for the scenario IDs.
"""
from __future__ import annotations

import pytest

from tests.conftest import auth_headers, fusion_alert_body


async def _alert(api_client, rig, **overrides) -> dict:
    resp = await api_client.post(
        "/api/alerts", json=fusion_alert_body(rig.route_id, **overrides), headers=rig.headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _get(api_client, user, alert_id) -> dict:
    return (await api_client.get(f"/api/alerts/{alert_id}", headers=auth_headers(user))).json()


async def test_post_resolve_by_device_key(api_client, root_admin, rig_a):
    alert = await _alert(api_client, rig_a)
    resp = await api_client.post(
        f"/api/alerts/{alert['id_alert']}/resolve",
        json={"resolved_at": "2030-01-01T09:00:00Z"},
        headers=rig_a.headers,
    )
    assert resp.status_code == 200
    assert (await _get(api_client, root_admin, alert["id_alert"]))["resolved_at"] is not None


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


async def test_resolve_twice_keeps_first_resolved_at(api_client, root_admin, rig_a):
    alert = await _alert(api_client, rig_a)
    for when in ("2030-01-01T09:00:00Z", "2030-01-01T10:00:00Z"):
        resp = await api_client.put(
            f"/api/alerts/{alert['id_alert']}", json={"resolved_at": when}, headers=rig_a.headers
        )
        assert resp.status_code == 200
    resolved = (await _get(api_client, root_admin, alert["id_alert"]))["resolved_at"]
    assert resolved.startswith("2030-01-01T09:00:00")


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


async def test_resolving_escalation_closes_linked_original(api_client, root_admin, rig_a):
    original = await _alert(api_client, rig_a, severity_level="medium", grip_status="good")
    escalation = await _alert(api_client, rig_a, related_alert_id=original["id_alert"])
    await api_client.put(
        f"/api/alerts/{escalation['id_alert']}",
        json={"resolved_at": "2030-01-01T09:00:00Z"},
        headers=rig_a.headers,
    )
    assert (await _get(api_client, root_admin, original["id_alert"]))["resolved_at"] is not None


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
    assert after["resolved_at"] is not None


async def test_resolve_then_deescalate_keeps_resolution(api_client, guardian, root_admin, rig_a):
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
    assert after["resolved_at"] is not None


async def test_panic_alert_can_be_resolved_by_guardian(api_client, guardian, root_admin, rig_a):
    """PAN-05: a panic alert never gets a device-side resolve, so a human must be able to."""
    alert = await _alert(
        api_client,
        rig_a,
        alert_type="panic_button",
        source="panic_button",
        ai_metadata=None,
        grip_status=None,
    )
    resp = await api_client.put(
        f"/api/alerts/{alert['id_alert']}",
        json={"resolved_at": "2030-01-01T09:00:00Z", "reviewed_by_operator": True},
        headers=auth_headers(guardian),
    )
    assert resp.status_code == 200
    assert (await _get(api_client, root_admin, alert["id_alert"]))["resolved_at"] is not None
