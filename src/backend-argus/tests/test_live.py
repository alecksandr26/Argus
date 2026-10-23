"""`GET /api/live` and `GET /api/routes/{id}/track` (docs/designs/final-tweaks-contracts.md,
C3.6)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.geo import GeoJSONPoint
from app.models.common import RouteStatus, Vigilance
from app.models.route import Route
from app.models.status_route import StatusRoute
from app.track import downsample
from tests.conftest import auth_headers, make_rig, post_report, report_event, report_point


async def _live(api_client, user, **params) -> dict:
    resp = await api_client.get("/api/live", params=params, headers=auth_headers(user))
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_live_needs_a_user(api_client, rig_a):
    assert (await api_client.get("/api/live")).status_code == 401
    assert (await api_client.get("/api/live", headers=rig_a.headers)).status_code == 401


async def test_live_shape(api_client, guardian, rig_a, rig_b):
    await post_report(
        api_client,
        rig_a,
        points=[report_point(age_s=3), report_point(age_s=1, last_known=True, location_age_s=30)],
        event=report_event(age_s=2),
    )
    body = await _live(api_client, guardian)
    assert body["server_time"].endswith(("Z", "+00:00"))
    routes = {r["id_route"]: r for r in body["routes"]}
    assert set(routes) == {rig_a.route_id, rig_b.route_id}
    a = routes[rig_a.route_id]
    assert a["incident_state"] == "critical"
    assert a["truck_plate_number"] == "RIG-A"
    assert a["latest_position"]["last_known"] is True
    assert a["latest_position"]["location_age_s"] == 30
    assert routes[rig_b.route_id]["latest_position"] is None
    assert routes[rig_b.route_id]["incident_state"] == "fine"
    assert len(body["incidents"]) == 1
    assert body["incidents"][0]["alerts"][0]["coordinates"] is not None
    assert [p["id_route"] for p in body["track"]] == [rig_a.route_id, rig_a.route_id]
    assert body["track"][0]["t"] < body["track"][1]["t"]
    assert set(body["track"][0]) == {
        "id_route", "t", "lat", "lon", "speed", "vigilance", "last_known", "location_age_s"
    }


async def test_track_without_since_is_the_last_15_minutes(api_client, guardian, rig_a):
    await post_report(api_client, rig_a, points=[report_point(age_s=20 * 60), report_point(age_s=60)])
    body = await _live(api_client, guardian)
    assert len(body["track"]) == 1


async def test_since_returns_only_points_received_after_it(api_client, guardian, rig_a):
    await post_report(api_client, rig_a, points=[report_point(age_s=5)])
    first = await _live(api_client, guardian)
    await post_report(api_client, rig_a, points=[report_point(age_s=2)])
    second = await _live(api_client, guardian, since=first["server_time"])
    assert len(first["track"]) == 1
    assert len(second["track"]) == 1
    assert second["track"][0]["t"] > first["track"][0]["t"]
    # Incidents and routes always come in full.
    assert len(second["routes"]) == 1


async def test_since_older_than_15_minutes_is_clamped(api_client, guardian, rig_a):
    old = StatusRoute(
        id_route=rig_a.route_id,
        current_coordinates=GeoJSONPoint(coordinates=(-103.0, 20.0)),
        current_speed=1.0,
        odometer=0.0,
        vigilance=Vigilance.LOW,
        timestamp=datetime.now(timezone.utc) - timedelta(minutes=30),
        received_at=datetime.now(timezone.utc) - timedelta(minutes=30),
    )
    await old.insert()
    since = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    assert (await _live(api_client, guardian, since=since))["track"] == []


async def test_malformed_since_is_422(api_client, guardian):
    resp = await api_client.get("/api/live", params={"since": "yesterday"}, headers=auth_headers(guardian))
    assert resp.status_code == 422


async def test_ended_route_leaves_routes_but_its_open_incident_stays(
    api_client, guardian, admin_user, rig_a
):
    await post_report(api_client, rig_a, points=[report_point()], event=report_event(age_s=5))
    await api_client.post(
        f"/api/routes/{rig_a.route_id}/stop", json={"reason": "x"}, headers=auth_headers(admin_user)
    )
    body = await _live(api_client, guardian)
    assert body["routes"] == []
    assert body["track"] == []
    assert len(body["incidents"]) == 1
    assert body["incidents"][0]["route_ended"] is True


async def test_route_filter(api_client, guardian, admin_user, rig_a, rig_b):
    await post_report(api_client, rig_a, points=[report_point()], event=report_event("a", age_s=5))
    await post_report(api_client, rig_b, points=[report_point()], event=report_event("b", age_s=5))
    await api_client.post(
        f"/api/routes/{rig_a.route_id}/stop", json={"reason": "x"}, headers=auth_headers(admin_user)
    )
    body = await _live(api_client, guardian, route=rig_a.route_id)
    assert [r["id_route"] for r in body["routes"]] == [rig_a.route_id]
    assert body["routes"][0]["operative_status"] == "cancelled"
    assert body["routes"][0]["stop_reason"] == "x"
    assert [i["id_route"] for i in body["incidents"]] == [rig_a.route_id]
    assert {p["id_route"] for p in body["track"]} == {rig_a.route_id}


async def test_route_filter_unknown_is_404(api_client, guardian):
    for bad in ("64b0a00000000000000000aa", "nope"):
        resp = await api_client.get("/api/live", params={"route": bad}, headers=auth_headers(guardian))
        assert resp.status_code == 404


async def test_closed_incidents_are_not_in_the_feed(api_client, guardian, rig_a):
    body = (await post_report(api_client, rig_a, event=report_event(age_s=5))).json()
    alert = (
        await api_client.get(f"/api/alerts/{body['event']['id_alert']}", headers=auth_headers(guardian))
    ).json()
    await api_client.post(
        f"/api/incidents/{alert['id_incident']}/close", json={"note": "ok"}, headers=auth_headers(guardian)
    )
    assert (await _live(api_client, guardian))["incidents"] == []


async def test_feed_is_tier_sorted(api_client, guardian, mongo_client):
    rig = await make_rig("FEED-1", departed_ago=timedelta(hours=5))
    await post_report(api_client, rig, event=report_event("old", age_s=3 * 3600))
    await post_report(api_client, rig, event=report_event("med", severity="medium", age_s=40 * 60))
    await post_report(api_client, rig, event=report_event("crit", age_s=60))
    tiers = [i["tier"] for i in (await _live(api_client, guardian))["incidents"]]
    assert tiers == ["critical", "medium", "old"]


# --- Track history ------------------------------------------------------------------------------


async def test_route_track_any_status(api_client, guardian, mongo_client):
    rig = await make_rig("TRK-1")
    await post_report(api_client, rig, points=[report_point(age_s=a) for a in (5, 4, 3)])
    await (await Route.get(rig.route_id)).set({"operative_status": RouteStatus.COMPLETED})
    resp = await api_client.get(f"/api/routes/{rig.route_id}/track", headers=auth_headers(guardian))
    body = resp.json()
    assert resp.status_code == 200
    assert body["id_route"] == rig.route_id
    assert body["downsampled"] is False
    assert len(body["points"]) == 3
    assert [p["t"] for p in body["points"]] == sorted(p["t"] for p in body["points"])


async def test_route_track_downsamples(api_client, guardian, rig_a):
    await post_report(api_client, rig_a, points=[report_point(age_s=a) for a in range(10, 0, -1)])
    resp = await api_client.get(
        f"/api/routes/{rig_a.route_id}/track", params={"max_points": 4}, headers=auth_headers(guardian)
    )
    body = resp.json()
    assert body["downsampled"] is True
    assert len(body["points"]) == 4


async def test_route_track_errors(api_client, guardian, rig_a):
    headers = auth_headers(guardian)
    assert (await api_client.get("/api/routes/64b0a00000000000000000aa/track", headers=headers)).status_code == 404
    for bad in (0, 10001):
        resp = await api_client.get(
            f"/api/routes/{rig_a.route_id}/track", params={"max_points": bad}, headers=headers
        )
        assert resp.status_code == 422
    assert (await api_client.get(f"/api/routes/{rig_a.route_id}/track")).status_code == 401


class _P:
    def __init__(self, i: int, vigilance: str = "low") -> None:
        self.i = i
        self.vigilance = Vigilance(vigilance)


def test_downsample_keeps_ends_and_notable_points():
    points = [_P(i) for i in range(100)]
    points[50] = _P(50, "critical")
    points[51] = _P(51, "unknown")
    kept = [p.i for p in downsample(points, 10)]
    assert len(kept) == 10
    assert kept[0] == 0 and kept[-1] == 99
    assert {50, 51} <= set(kept)
    assert kept == sorted(kept)


def test_downsample_under_the_limit_is_unchanged():
    points = [_P(i) for i in range(5)]
    assert downsample(points, 5) == points


def test_downsample_with_too_many_notable_points_keeps_the_newest():
    points = [_P(i, "medium") for i in range(20)]
    kept = [p.i for p in downsample(points, 5)]
    assert kept == [0, 16, 17, 18, 19]
