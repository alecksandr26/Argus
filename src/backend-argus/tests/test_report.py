"""`POST /api/devices/me/report`, the one device write (docs/designs/final-tweaks-contracts.md,
C2): envelope checks, per-part results, points, events routed by time, dedup, driver OK."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.models.alert import Alert
from app.models.common import RouteStatus
from app.models.incident import DriverOk
from app.models.route import Route
from app.models.status_route import StatusRoute
from tests.conftest import (
    REPORT_URL,
    auth_headers,
    make_rig,
    ok_event,
    post_report,
    report_event,
    report_point,
)


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# --- Envelope, auth ----------------------------------------------------------------------------


async def test_points_and_event_get_a_result_each(api_client, rig_a):
    resp = await post_report(
        api_client, rig_a, points=[report_point(age_s=3), report_point(age_s=1)], event=report_event()
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["points"] == {"stored": 2, "route": rig_a.route_id, "status": "ok"}
    assert body["event"]["status"] == "created"
    assert body["event"]["reason"] is None
    assert body["event"]["id_alert"]
    assert body["active_route"] == rig_a.route_id


async def test_points_only_report_has_null_event(api_client, rig_a):
    body = (await post_report(api_client, rig_a, points=[report_point()])).json()
    assert body["event"] is None
    assert body["points"]["stored"] == 1


async def test_empty_points_list_is_fine(api_client, rig_a):
    resp = await post_report(api_client, rig_a, points=[])
    assert resp.status_code == 200
    assert resp.json()["points"] == {"stored": 0, "route": rig_a.route_id, "status": "ok"}


@pytest.mark.parametrize(
    "body",
    [
        {},  # points is required
        {"points": "nope"},
        {"points": [report_point()] * 11},  # REPORT_MAX_POINTS = 10
        {"points": [], "event": "critical"},
    ],
    ids=["no_points", "points_not_list", "eleven_points", "event_not_object"],
)
async def test_bad_envelope_is_422(api_client, rig_a, body):
    resp = await api_client.post(REPORT_URL, json=body, headers=rig_a.headers)
    assert resp.status_code == 422


@pytest.mark.parametrize("raw", ["not json", "[1, 2]"], ids=["not_json", "not_object"])
async def test_non_object_body_is_422(api_client, rig_a, raw):
    resp = await api_client.post(
        REPORT_URL, content=raw, headers={**rig_a.headers, "Content-Type": "application/json"}
    )
    assert resp.status_code == 422


async def test_unknown_top_level_keys_are_ignored(api_client, rig_a):
    resp = await api_client.post(
        REPORT_URL, json={"points": [], "firmware": "1.2.3"}, headers=rig_a.headers
    )
    assert resp.status_code == 200


async def test_body_over_16_kib_is_413(api_client, rig_a):
    body = json.dumps({"points": [], "padding": "x" * (16 * 1024)})
    resp = await api_client.post(
        REPORT_URL, content=body, headers={**rig_a.headers, "Content-Type": "application/json"}
    )
    assert resp.status_code == 413


@pytest.mark.parametrize("headers", [{}, {"X-Device-Api-Key": "wrong"}], ids=["none", "wrong"])
async def test_bad_or_missing_key_is_401(api_client, rig_a, headers):
    resp = await api_client.post(REPORT_URL, json={"points": []}, headers=headers)
    assert resp.status_code == 401


async def test_user_token_is_not_accepted(api_client, rig_a, root_admin):
    resp = await api_client.post(REPORT_URL, json={"points": []}, headers=auth_headers(root_admin))
    assert resp.status_code == 401


# --- Points -------------------------------------------------------------------------------------


async def test_invalid_points_are_skipped_not_422(api_client, rig_a):
    points = [
        report_point(age_s=5),
        report_point(age_s=4, lat=95.0),
        report_point(age_s=3, vigilance="sleepy"),
        report_point(age_s=2, speed=-1),
        "not an object",
        report_point(age_s=1),
    ]
    resp = await post_report(api_client, rig_a, points=points)
    assert resp.status_code == 200
    assert resp.json()["points"]["stored"] == 2


async def test_point_fields_are_stored(api_client, rig_a):
    await post_report(
        api_client,
        rig_a,
        points=[report_point(vigilance="unknown", last_known=True, location_age_s=42, speed=0)],
    )
    point = await StatusRoute.find_one(StatusRoute.id_route == rig_a.route_id)
    assert point.vigilance == "unknown"
    assert point.location_is_last_known is True
    assert point.location_age_s == 42
    assert point.time_source == "relative"
    assert point.timestamp.microsecond == 0  # rounded to the second


async def test_point_timestamp_is_received_at_minus_age(api_client, rig_a):
    before = datetime.now(timezone.utc)
    await post_report(api_client, rig_a, points=[report_point(age_s=30)])
    point = await StatusRoute.find_one(StatusRoute.id_route == rig_a.route_id)
    stamped = point.timestamp.replace(tzinfo=timezone.utc)
    assert before - timedelta(seconds=32) <= stamped <= before - timedelta(seconds=28)


async def test_points_at_the_same_second_are_stored_once(api_client, rig_a):
    resp = await post_report(api_client, rig_a, points=[report_point(age_s=2), report_point(age_s=2)])
    assert resp.json()["points"]["stored"] == 1
    assert await StatusRoute.find(StatusRoute.id_route == rig_a.route_id).count() == 1


async def test_no_active_route_stores_nothing(api_client, mongo_client):
    rig = await make_rig("SCHED-R", route_status=RouteStatus.SCHEDULED)
    body = (await post_report(api_client, rig, points=[report_point()])).json()
    assert body["points"] == {"stored": 0, "route": None, "status": "no_active_route"}
    assert body["active_route"] is None
    assert await StatusRoute.find_all().count() == 0


@pytest.mark.parametrize("finished", [RouteStatus.COMPLETED, RouteStatus.CANCELLED])
async def test_finished_route_gets_route_finished(api_client, mongo_client, finished):
    rig = await make_rig("DONE-R", route_status=finished)
    body = (await post_report(api_client, rig, points=[report_point()])).json()
    assert body["points"] == {"stored": 0, "route": None, "status": "route_finished"}


async def test_points_go_to_the_newest_active_route(api_client, rig_a):
    """Two routes in progress (a data entry mistake): the newest departure wins."""
    older = await Route.get(rig_a.route_id)
    newer = Route(
        **older.model_dump(exclude={"id", "revision_id"}),
    )
    newer.actual_departure = datetime.now(timezone.utc) - timedelta(minutes=5)
    await newer.insert()
    body = (await post_report(api_client, rig_a, points=[report_point()])).json()
    assert body["points"]["route"] == str(newer.id)
    assert body["active_route"] == str(newer.id)


# --- Events -------------------------------------------------------------------------------------


async def test_alert_event_fields(api_client, root_admin, rig_a):
    before = datetime.now(timezone.utc)
    body = (
        await post_report(
            api_client,
            rig_a,
            event=report_event(
                age_s=12.5,
                delivery="dlq",
                queued_seconds=37,
                position={"lat": 20.1, "lon": -103.2, "last_known": True, "location_age_s": 90},
            ),
        )
    ).json()
    alert = (
        await api_client.get(
            f"/api/alerts/{body['event']['id_alert']}", headers=auth_headers(root_admin)
        )
    ).json()
    assert alert["id_route"] == rig_a.route_id
    assert alert["source"] == "fusion"
    assert alert["severity_level"] == "critical"
    assert alert["alert_type"] == "eyes_closed"
    assert alert["episode"] == "a1b2c3d4e5f6"
    assert alert["client_event_id"] == "evt-1"
    assert alert["delivery"] == "dlq"
    assert alert["queued_seconds"] == 37
    assert alert["coordinates"] == {"lat": 20.1, "lon": -103.2}
    assert alert["position_unknown"] is False
    assert alert["location_is_last_known"] is True
    assert alert["location_age_s"] == 90
    assert alert["speed_at_event"] == 62.5
    assert alert["time_source"] == "relative"
    assert alert["grip_status"] is None
    assert alert["ai_metadata"]["scores"] == {"not_drowsy": 0.05, "drowsy": 0.95}
    assert alert["id_incident"]
    occurred = _utc(alert["timestamp"])
    expected = before - timedelta(seconds=12.5)
    assert abs((occurred - expected).total_seconds()) < 2


async def test_event_without_position_is_position_unknown(api_client, root_admin, rig_a):
    body = (
        await post_report(
            api_client, rig_a, event=report_event(position=None, speed=None, ai_metadata=None)
        )
    ).json()
    alert = (
        await api_client.get(
            f"/api/alerts/{body['event']['id_alert']}", headers=auth_headers(root_admin)
        )
    ).json()
    assert body["event"]["status"] == "created"
    assert alert["coordinates"] is None
    assert alert["position_unknown"] is True
    assert alert["speed_at_event"] is None
    assert alert["ai_metadata"] is None


@pytest.mark.parametrize(
    "event",
    [
        report_event(severity=None),
        report_event(alert_type=None),
        report_event(severity="low"),
        report_event(kind="panic"),
        report_event(delivery="later"),
        report_event(age_s=-1),
        report_event(client_event_id=""),
        report_event(client_event_id="x" * 65),
        report_event(position={"lat": 91, "lon": 0, "last_known": False, "location_age_s": 0}),
        ok_event(severity="critical"),
        {"kind": "alert"},
    ],
    ids=[
        "alert_no_severity",
        "alert_no_type",
        "low_severity",
        "unknown_kind",
        "bad_delivery",
        "negative_age",
        "empty_id",
        "long_id",
        "bad_position",
        "ok_with_severity",
        "bare",
    ],
)
async def test_invalid_event_is_rejected_but_points_are_stored(api_client, rig_a, event):
    resp = await post_report(api_client, rig_a, points=[report_point()], event=event)
    assert resp.status_code == 200
    body = resp.json()
    assert body["event"] == {"status": "rejected", "reason": "invalid", "id_alert": None}
    assert body["points"]["stored"] == 1
    assert await Alert.find_all().count() == 0


async def test_event_older_than_seven_days_is_too_old(api_client, rig_a):
    body = (await post_report(api_client, rig_a, event=report_event(age_s=604801))).json()
    assert body["event"] == {"status": "rejected", "reason": "too_old", "id_alert": None}


async def test_retry_with_same_id_is_duplicate(api_client, rig_a):
    first = (await post_report(api_client, rig_a, event=report_event())).json()
    second = (await post_report(api_client, rig_a, event=report_event(age_s=9))).json()
    assert first["event"]["status"] == "created"
    assert second["event"] == {
        "status": "duplicate",
        "reason": None,
        "id_alert": first["event"]["id_alert"],
    }
    assert await Alert.find_all().count() == 1


async def test_concurrent_duplicates_store_one_alert(api_client, rig_a):
    """A retry racing the original (lost HTTP response): the per-route lock plus the unique
    (id_route, client_event_id) index leave exactly one alert and one incident."""
    responses = await asyncio.gather(
        *(post_report(api_client, rig_a, event=report_event()) for _ in range(4))
    )
    statuses = sorted(r.json()["event"]["status"] for r in responses)
    assert statuses == ["created", "duplicate", "duplicate", "duplicate"]
    assert len({r.json()["event"]["id_alert"] for r in responses}) == 1
    assert await Alert.find_all().count() == 1


async def test_same_event_id_from_another_truck_is_wrong_truck(api_client, rig_a, rig_b):
    await post_report(api_client, rig_a, event=report_event("shared-id"))
    body = (await post_report(api_client, rig_b, event=report_event("shared-id"))).json()
    assert body["event"] == {"status": "rejected", "reason": "wrong_truck", "id_alert": None}


async def test_event_is_routed_by_time_not_to_the_current_route(api_client, mongo_client):
    """A DLQ replay of an event from a route that has since ended is rejected route_finished,
    never stamped with the truck's current route (the old firmware's bug)."""
    rig = await make_rig("TIME-1")
    old = await Route.get(rig.route_id)
    now = datetime.now(timezone.utc)
    await old.set(
        {
            "operative_status": RouteStatus.COMPLETED,
            "actual_departure": now - timedelta(hours=3),
            "actual_arrival": now - timedelta(hours=2),
        }
    )
    current = Route(**old.model_dump(exclude={"id", "revision_id"}))
    current.operative_status = RouteStatus.IN_PROGRESS
    current.actual_departure = now - timedelta(hours=1)
    current.actual_arrival = None
    await current.insert()

    during_old = (await post_report(api_client, rig, event=report_event("e1", age_s=9000))).json()
    during_current = (await post_report(api_client, rig, event=report_event("e2", age_s=60))).json()
    between = (await post_report(api_client, rig, event=report_event("e3", age_s=5400))).json()
    before_any = (await post_report(api_client, rig, event=report_event("e4", age_s=20000))).json()

    assert during_old["event"]["reason"] == "route_finished"
    assert during_current["event"]["status"] == "created"
    stored = await Alert.get(during_current["event"]["id_alert"])
    assert stored.id_route == str(current.id)
    assert between["event"]["reason"] == "no_route"
    assert before_any["event"]["reason"] == "no_route"


async def test_stopped_route_rejects_points_events_and_ok(api_client, admin_user, rig_a):
    created = (await post_report(api_client, rig_a, event=report_event("before-stop"))).json()
    stop = await api_client.post(
        f"/api/routes/{rig_a.route_id}/stop",
        json={"reason": "truck broke down"},
        headers=auth_headers(admin_user),
    )
    assert stop.status_code == 200
    # Events that happened during the route but reach the backend after the stop (a replay).
    late = (
        await post_report(
            api_client, rig_a, points=[report_point()], event=report_event("late", age_s=60)
        )
    ).json()
    ok = (await post_report(api_client, rig_a, event=ok_event(age_s=30))).json()
    # An event that happened after the stop belongs to no route at all (C2.2 rule 4).
    after = (await post_report(api_client, rig_a, event=report_event("after", age_s=0))).json()
    assert created["event"]["status"] == "created"
    assert late["points"] == {"stored": 0, "route": None, "status": "route_finished"}
    assert late["event"]["reason"] == "route_finished"
    assert ok["event"]["reason"] == "route_finished"
    assert after["event"]["reason"] == "no_route"


async def test_escalation_links_to_the_medium_of_its_episode(api_client, rig_a):
    medium = (
        await post_report(api_client, rig_a, event=report_event("m1", severity="medium", age_s=30))
    ).json()
    critical = (await post_report(api_client, rig_a, event=report_event("c1", age_s=10))).json()
    escalation = await Alert.get(critical["event"]["id_alert"])
    original = await Alert.get(medium["event"]["id_alert"])
    assert escalation.related_alert_id == str(original.id)
    assert escalation.id_incident == original.id_incident


# --- Driver OK ----------------------------------------------------------------------------------


async def test_driver_ok_sets_driver_ok_at_and_closes_nothing(api_client, root_admin, rig_a):
    alert = (await post_report(api_client, rig_a, event=report_event(age_s=30))).json()
    ok = (await post_report(api_client, rig_a, event=ok_event(age_s=5))).json()
    assert ok["event"] == {
        "status": "created",
        "reason": None,
        "id_alert": alert["event"]["id_alert"],
    }
    stored = await Alert.get(alert["event"]["id_alert"])
    incident = (
        await api_client.get(f"/api/incidents/{stored.id_incident}", headers=auth_headers(root_admin))
    ).json()
    route = await Route.get(rig_a.route_id)
    assert stored.driver_ok_at is not None
    assert stored.closed_at is None
    assert incident["closed_at"] is None
    assert incident["driver_ok_at"] is not None
    assert route.incident_state == "critical"


async def test_driver_ok_before_its_alert_still_applies(api_client, rig_a):
    """The OK can arrive first (the critical waited in the DLQ): it's stored and the alert
    picks it up when it lands."""
    ok = (await post_report(api_client, rig_a, event=ok_event(age_s=5))).json()
    assert ok["event"] == {"status": "created", "reason": None, "id_alert": None}
    alert = (
        await post_report(api_client, rig_a, event=report_event(age_s=60, delivery="dlq"))
    ).json()
    stored = await Alert.get(alert["event"]["id_alert"])
    assert stored.driver_ok_at is not None
    assert await DriverOk.find_all().count() == 1


async def test_driver_ok_retry_is_duplicate(api_client, rig_a):
    await post_report(api_client, rig_a, event=ok_event())
    again = (await post_report(api_client, rig_a, event=ok_event())).json()
    assert again["event"]["status"] == "duplicate"
    assert await DriverOk.find_all().count() == 1


async def test_driver_ok_of_another_episode_changes_nothing(api_client, rig_a):
    alert = (await post_report(api_client, rig_a, event=report_event(age_s=30))).json()
    await post_report(api_client, rig_a, event=ok_event(episode="ffffffffffff"))
    assert (await Alert.get(alert["event"]["id_alert"])).driver_ok_at is None
