"""Incident grouping, closing, and the incident endpoints
(docs/designs/final-tweaks-contracts.md, C3.4/C3.6)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.incidents import feed_sort_key, incident_tier
from app.models.alert import Alert
from app.models.common import Severity
from app.models.incident import Incident
from app.models.route import Route
from tests.conftest import auth_headers, make_rig, post_report, report_event

MIN = 60.0


async def _alert(api_client, rig, event_id: str, age_s: float, severity: str = "critical") -> Alert:
    body = (
        await post_report(
            api_client,
            rig,
            event=report_event(event_id, age_s=age_s, severity=severity, episode=event_id[:12]),
        )
    ).json()
    assert body["event"]["status"] == "created", body
    return await Alert.get(body["event"]["id_alert"])


@pytest.fixture
async def rig(mongo_client):
    return await make_rig("INC-1", departed_ago=timedelta(hours=5))


# --- Grouping -----------------------------------------------------------------------------------


async def test_alert_14_min_after_joins(api_client, rig):
    a = await _alert(api_client, rig, "a", age_s=60 * MIN)
    b = await _alert(api_client, rig, "b", age_s=46 * MIN)
    assert a.id_incident == b.id_incident
    incident = await Incident.get(a.id_incident)
    assert incident.alert_count == 2


async def test_alert_16_min_after_starts_a_new_incident(api_client, rig):
    a = await _alert(api_client, rig, "a", age_s=60 * MIN)
    b = await _alert(api_client, rig, "b", age_s=44 * MIN)
    assert a.id_incident != b.id_incident


async def test_window_restarts_with_each_alert(api_client, rig):
    a = await _alert(api_client, rig, "a", age_s=60 * MIN)
    b = await _alert(api_client, rig, "b", age_s=46 * MIN)
    c = await _alert(api_client, rig, "c", age_s=32 * MIN)  # 28 min after a, 14 after b
    assert a.id_incident == b.id_incident == c.id_incident
    incident = await Incident.get(a.id_incident)
    assert incident.alert_count == 3


async def test_late_alert_before_the_first_joins(api_client, rig):
    """A DLQ replay that happened 10 min before the incident's first alert still joins it, and
    becomes its first alert."""
    a = await _alert(api_client, rig, "a", age_s=30 * MIN)
    late = await _alert(api_client, rig, "late", age_s=40 * MIN)
    incident = await Incident.get(a.id_incident)
    assert late.id_incident == a.id_incident
    assert incident.first_alert_at.replace(tzinfo=timezone.utc) == late.timestamp.replace(
        tzinfo=timezone.utc
    )


async def test_late_alert_never_joins_a_closed_incident(api_client, guardian, rig):
    a = await _alert(api_client, rig, "a", age_s=30 * MIN)
    closed = await api_client.post(
        f"/api/incidents/{a.id_incident}/close",
        json={"note": "called the driver"},
        headers=auth_headers(guardian),
    )
    assert closed.status_code == 200
    b = await _alert(api_client, rig, "b", age_s=25 * MIN)
    assert b.id_incident != a.id_incident
    assert (await Incident.get(b.id_incident)).closed_at is None


async def test_alerts_of_other_routes_never_join(api_client, rig, mongo_client):
    other = await make_rig("INC-2", departed_ago=timedelta(hours=5))
    a = await _alert(api_client, rig, "a", age_s=10 * MIN)
    b = await _alert(api_client, other, "b", age_s=10 * MIN)
    assert a.id_incident != b.id_incident


async def test_incident_aggregates(api_client, rig):
    medium = await _alert(api_client, rig, "m", age_s=20 * MIN, severity="medium")
    incident = await Incident.get(medium.id_incident)
    assert incident.severity == Severity.MEDIUM
    assert incident.has_replayed is False
    body = (
        await post_report(
            api_client, rig, event=report_event("c", age_s=10 * MIN, delivery="dlq", queued_seconds=50)
        )
    ).json()
    critical = await Alert.get(body["event"]["id_alert"])
    incident = await Incident.get(medium.id_incident)
    assert critical.id_incident == medium.id_incident
    assert incident.severity == Severity.CRITICAL
    assert incident.has_replayed is True
    assert incident.alert_count == 2
    assert incident.last_alert_at.replace(tzinfo=timezone.utc) == critical.timestamp.replace(
        tzinfo=timezone.utc
    )


async def test_legacy_alert_create_groups_too(api_client, rig):
    from tests.conftest import fusion_alert_body

    first = await api_client.post(
        "/api/alerts", json=fusion_alert_body(rig.route_id), headers=rig.headers
    )
    second = await api_client.post(
        "/api/alerts",
        json=fusion_alert_body(rig.route_id, severity_level="medium"),
        headers=rig.headers,
    )
    assert first.json()["id_incident"] is not None
    assert first.json()["id_incident"] == second.json()["id_incident"]


# --- Closing ------------------------------------------------------------------------------------


async def test_close_critical_needs_a_note(api_client, guardian, rig):
    a = await _alert(api_client, rig, "a", age_s=5 * MIN)
    for note in ("", "   "):
        resp = await api_client.post(
            f"/api/incidents/{a.id_incident}/close",
            json={"note": note},
            headers=auth_headers(guardian),
        )
        assert resp.status_code == 422
        assert resp.json()["detail"] == "A note is required to close a critical incident"
    assert (await Incident.get(a.id_incident)).closed_at is None


async def test_close_medium_without_note(api_client, root_admin, rig):
    a = await _alert(api_client, rig, "a", age_s=5 * MIN, severity="medium")
    resp = await api_client.post(
        f"/api/incidents/{a.id_incident}/close", json={}, headers=auth_headers(root_admin)
    )
    assert resp.status_code == 200
    assert resp.json()["close_note"] == ""


async def test_close_sets_fields_on_incident_and_alerts(api_client, guardian, rig):
    a = await _alert(api_client, rig, "a", age_s=10 * MIN, severity="medium")
    b = await _alert(api_client, rig, "b", age_s=5 * MIN)
    resp = await api_client.post(
        f"/api/incidents/{a.id_incident}/close",
        json={"note": "  driver pulled over  "},
        headers=auth_headers(guardian),
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["closed_at"] is not None
    assert body["closed_by"] == str(guardian.id)
    assert body["close_note"] == "driver pulled over"
    assert [x["closed_at"] is not None for x in body["alerts"]] == [True, True]
    for alert in (a, b):
        stored = await Alert.get(alert.id)
        assert stored.closed_at is not None
        assert stored.reviewed_by_operator is True


async def test_close_twice_is_409(api_client, guardian, rig):
    a = await _alert(api_client, rig, "a", age_s=5 * MIN)
    url = f"/api/incidents/{a.id_incident}/close"
    first = await api_client.post(url, json={"note": "ok"}, headers=auth_headers(guardian))
    second = await api_client.post(url, json={"note": "ok"}, headers=auth_headers(guardian))
    assert (first.status_code, second.status_code) == (200, 409)


async def test_close_unknown_is_404(api_client, guardian):
    resp = await api_client.post(
        "/api/incidents/64b0a00000000000000000aa/close",
        json={"note": "x"},
        headers=auth_headers(guardian),
    )
    assert resp.status_code == 404


async def test_close_note_too_long_is_422(api_client, guardian, rig):
    a = await _alert(api_client, rig, "a", age_s=5 * MIN)
    resp = await api_client.post(
        f"/api/incidents/{a.id_incident}/close",
        json={"note": "x" * 1001},
        headers=auth_headers(guardian),
    )
    assert resp.status_code == 422


async def test_close_roles(api_client, admin_user, truck_driver_user, rig):
    a = await _alert(api_client, rig, "a", age_s=5 * MIN)
    url = f"/api/incidents/{a.id_incident}/close"
    body = {"note": "x"}
    assert (await api_client.post(url, json=body)).status_code == 401
    assert (await api_client.post(url, json=body, headers=rig.headers)).status_code == 401
    for user in (admin_user, truck_driver_user):
        assert (await api_client.post(url, json=body, headers=auth_headers(user))).status_code == 403


async def test_closing_lowers_route_state_to_remaining_open_incidents(api_client, guardian, rig):
    medium = await _alert(api_client, rig, "m", age_s=100 * MIN, severity="medium")
    critical = await _alert(api_client, rig, "c", age_s=10 * MIN)
    assert medium.id_incident != critical.id_incident
    assert (await Route.get(rig.route_id)).incident_state == "critical"

    await api_client.post(
        f"/api/incidents/{critical.id_incident}/close",
        json={"note": "ok"},
        headers=auth_headers(guardian),
    )
    assert (await Route.get(rig.route_id)).incident_state == "medium"
    await api_client.post(
        f"/api/incidents/{medium.id_incident}/close", json={}, headers=auth_headers(guardian)
    )
    assert (await Route.get(rig.route_id)).incident_state == "fine"


async def test_closing_never_raises_route_state(api_client, guardian, rig):
    """A route already cleared to fine by OK points stays fine when an incident closes."""
    critical = await _alert(api_client, rig, "c", age_s=10 * MIN)
    await _alert(api_client, rig, "c2", age_s=100 * MIN)  # another open critical incident
    await (await Route.get(rig.route_id)).set({"incident_state": "fine"})
    await api_client.post(
        f"/api/incidents/{critical.id_incident}/close",
        json={"note": "ok"},
        headers=auth_headers(guardian),
    )
    assert (await Route.get(rig.route_id)).incident_state == "fine"


# --- Reading ------------------------------------------------------------------------------------


async def test_get_incident_shape(api_client, truck_driver_user, rig):
    a = await _alert(api_client, rig, "a", age_s=5 * MIN)
    resp = await api_client.get(
        f"/api/incidents/{a.id_incident}", headers=auth_headers(truck_driver_user)
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["id_incident"] == a.id_incident
    assert body["id_route"] == rig.route_id
    assert body["severity"] == "critical"
    assert body["tier"] == "critical"
    assert body["alert_count"] == 1
    assert body["route_ended"] is False
    assert body["truck_plate_number"] == "INC-1"
    assert body["driver_full_name"] == "Edge INC-1"
    assert body["closed_at"] is None and body["close_note"] == ""
    assert body["alerts"][0]["id_alert"] == str(a.id)
    assert body["alerts"][0]["coordinates"] == {"lat": 20.6597, "lon": -103.3496}


async def test_incident_endpoints_need_a_user(api_client, rig):
    a = await _alert(api_client, rig, "a", age_s=5 * MIN)
    assert (await api_client.get("/api/incidents")).status_code == 401
    assert (
        await api_client.get(f"/api/incidents/{a.id_incident}", headers=rig.headers)
    ).status_code == 401


async def test_list_incidents_filters(api_client, guardian, rig, mongo_client):
    other = await make_rig("INC-3", departed_ago=timedelta(hours=5))
    a = await _alert(api_client, rig, "a", age_s=100 * MIN)
    b = await _alert(api_client, rig, "b", age_s=10 * MIN)
    c = await _alert(api_client, other, "c", age_s=5 * MIN)
    await api_client.post(
        f"/api/incidents/{a.id_incident}/close", json={"note": "x"}, headers=auth_headers(guardian)
    )
    headers = auth_headers(guardian)

    async def ids(**params):
        resp = await api_client.get("/api/incidents", params=params, headers=headers)
        assert resp.status_code == 200
        return [i["id_incident"] for i in resp.json()]

    assert await ids() == [c.id_incident, b.id_incident, a.id_incident]  # newest first
    assert await ids(status="open") == [c.id_incident, b.id_incident]
    assert await ids(status="closed") == [a.id_incident]
    assert await ids(route_id=rig.route_id, status="open") == [b.id_incident]
    assert await ids(limit=1) == [c.id_incident]
    bad = await api_client.get("/api/incidents", params={"status": "nope"}, headers=headers)
    assert bad.status_code == 422


async def test_alerts_can_be_listed_by_incident(api_client, guardian, rig):
    a = await _alert(api_client, rig, "a", age_s=10 * MIN)
    await _alert(api_client, rig, "b", age_s=100 * MIN)
    resp = await api_client.get(
        "/api/alerts", params={"incident_id": a.id_incident}, headers=auth_headers(guardian)
    )
    assert [x["id_alert"] for x in resp.json()] == [str(a.id)]


async def test_deleting_the_last_alert_deletes_its_incident(api_client, root_admin, rig):
    a = await _alert(api_client, rig, "a", age_s=10 * MIN)
    resp = await api_client.delete(f"/api/alerts/{a.id}", headers=auth_headers(root_admin))
    assert resp.status_code == 200
    assert await Incident.get(a.id_incident) is None


# --- Tiers --------------------------------------------------------------------------------------


def _incident(severity: Severity, last_alert_ago: timedelta, now: datetime) -> Incident:
    t = now - last_alert_ago
    return Incident(id_route="r", severity=severity, first_alert_at=t, last_alert_at=t)


def test_tier_is_old_after_an_hour():
    now = datetime(2030, 1, 1, 12, tzinfo=timezone.utc)
    assert incident_tier(_incident(Severity.CRITICAL, timedelta(minutes=59), now), now) == "critical"
    assert incident_tier(_incident(Severity.MEDIUM, timedelta(minutes=59), now), now) == "medium"
    assert incident_tier(_incident(Severity.CRITICAL, timedelta(minutes=61), now), now) == "old"


def test_feed_order_is_tier_then_newest():
    now = datetime(2030, 1, 1, 12, tzinfo=timezone.utc)
    rows = [
        ("old-new", "old", now - timedelta(hours=2)),
        ("med", "medium", now - timedelta(minutes=1)),
        ("crit-old", "critical", now - timedelta(minutes=30)),
        ("old-older", "old", now - timedelta(hours=5)),
        ("crit-new", "critical", now - timedelta(minutes=2)),
    ]
    ordered = [name for name, tier, t in sorted(rows, key=lambda r: feed_sort_key(r[1], r[2]))]
    assert ordered == ["crit-new", "crit-old", "med", "old-new", "old-older"]
