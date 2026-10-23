"""Route `incident_state` (docs/designs/final-tweaks-contracts.md, C3.5) and GPS point
retention (`keep`, C3.3)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.incidents import apply_points_to_route, raise_route_state
from app.indexes import POINT_TTL
from app.models.common import Severity, Vigilance
from app.models.route import Route
from app.models.status_route import StatusRoute
from app.report import store_points
from app.geo import GeoJSONPoint
from tests.conftest import post_report, report_event, report_point

T0 = datetime(2030, 1, 1, 8, tzinfo=timezone.utc)


def _ok_run(start: datetime, minutes: float, step_s: int = 30, vigilance=Vigilance.LOW):
    n = int(minutes * 60 / step_s) + 1
    return [(start + timedelta(seconds=i * step_s), vigilance) for i in range(n)]


async def _state(route_id: str) -> str:
    return (await Route.get(route_id)).incident_state.value


async def _critical(route_id: str, at: datetime = T0) -> None:
    await raise_route_state(route_id, Severity.CRITICAL, at)


async def test_alert_raises_state_and_never_lowers_it(rig_a):
    await raise_route_state(rig_a.route_id, Severity.MEDIUM, T0)
    assert await _state(rig_a.route_id) == "medium"
    await raise_route_state(rig_a.route_id, Severity.CRITICAL, T0 + timedelta(minutes=1))
    assert await _state(rig_a.route_id) == "critical"
    await raise_route_state(rig_a.route_id, Severity.MEDIUM, T0 + timedelta(minutes=2))
    route = await Route.get(rig_a.route_id)
    assert route.incident_state == "critical"
    assert route.last_not_ok_at.replace(tzinfo=timezone.utc) == T0 + timedelta(minutes=2)


async def test_an_hour_of_ok_points_clears_to_fine(rig_a):
    await _critical(rig_a.route_id)
    await apply_points_to_route(rig_a.route_id, _ok_run(T0 + timedelta(minutes=1), 59))
    assert await _state(rig_a.route_id) == "critical"
    await apply_points_to_route(rig_a.route_id, _ok_run(T0 + timedelta(minutes=60, seconds=30), 1))
    assert await _state(rig_a.route_id) == "fine"


async def test_points_spread_over_several_reports_count(rig_a):
    await _critical(rig_a.route_id)
    run = _ok_run(T0 + timedelta(minutes=1), 61, step_s=5)
    for i in range(0, len(run), 10):  # one report every 10 points, like the ESP32
        await apply_points_to_route(rig_a.route_id, run[i : i + 10])
    assert await _state(rig_a.route_id) == "fine"


async def test_a_gap_over_60_s_restarts_the_hour(rig_a):
    await _critical(rig_a.route_id)
    first = _ok_run(T0 + timedelta(minutes=1), 30)
    after_gap = _ok_run(first[-1][0] + timedelta(seconds=61), 40)
    await apply_points_to_route(rig_a.route_id, first + after_gap)
    assert await _state(rig_a.route_id) == "critical"
    route = await Route.get(rig_a.route_id)
    assert route.ok_since.replace(tzinfo=timezone.utc) == after_gap[0][0]


async def test_unknown_vigilance_breaks_the_run(rig_a):
    await _critical(rig_a.route_id)
    run = _ok_run(T0 + timedelta(minutes=1), 70)
    run[60] = (run[60][0], Vigilance.UNKNOWN)
    await apply_points_to_route(rig_a.route_id, run)
    assert await _state(rig_a.route_id) == "critical"


async def test_silence_never_clears(rig_a):
    """An hour (or a day) without any point changes nothing."""
    await _critical(rig_a.route_id)
    await apply_points_to_route(rig_a.route_id, _ok_run(T0 + timedelta(minutes=1), 1))
    await apply_points_to_route(rig_a.route_id, _ok_run(T0 + timedelta(hours=25), 1))
    assert await _state(rig_a.route_id) == "critical"


async def test_only_unknown_points_never_clear(rig_a):
    await _critical(rig_a.route_id)
    await apply_points_to_route(
        rig_a.route_id, _ok_run(T0 + timedelta(minutes=1), 120, vigilance=Vigilance.UNKNOWN)
    )
    assert await _state(rig_a.route_id) == "critical"


async def test_points_never_raise_the_state(rig_a):
    await apply_points_to_route(
        rig_a.route_id, _ok_run(T0, 5, vigilance=Vigilance.CRITICAL)
    )
    route = await Route.get(rig_a.route_id)
    assert route.incident_state == "fine"
    assert route.last_not_ok_at.replace(tzinfo=timezone.utc) == T0 + timedelta(minutes=5)


async def test_a_new_alert_restarts_the_ok_run(rig_a):
    await _critical(rig_a.route_id)
    await apply_points_to_route(rig_a.route_id, _ok_run(T0 + timedelta(minutes=1), 50))
    await raise_route_state(rig_a.route_id, Severity.MEDIUM, T0 + timedelta(minutes=51))
    await apply_points_to_route(rig_a.route_id, _ok_run(T0 + timedelta(minutes=51, seconds=30), 30))
    assert await _state(rig_a.route_id) == "critical"


async def test_late_points_never_extend_the_run(rig_a):
    await _critical(rig_a.route_id)
    await apply_points_to_route(rig_a.route_id, _ok_run(T0 + timedelta(minutes=30), 1))
    # Points older than the newest OK point already counted can't make the run longer.
    await apply_points_to_route(rig_a.route_id, _ok_run(T0 - timedelta(hours=2), 120))
    assert await _state(rig_a.route_id) == "critical"


async def test_report_points_feed_the_route_state(api_client, rig_a):
    await post_report(api_client, rig_a, event=report_event(age_s=5))
    await post_report(api_client, rig_a, points=[report_point(vigilance="low", age_s=1)])
    route = await Route.get(rig_a.route_id)
    assert route.incident_state == "critical"
    assert route.ok_since is not None


# --- Retention: keep flag and TTL index --------------------------------------------------------


def _point(route_id: str, at: datetime) -> StatusRoute:
    return StatusRoute(
        id_route=route_id,
        current_coordinates=GeoJSONPoint(coordinates=(-103.0, 20.0)),
        current_speed=50.0,
        odometer=0.0,
        vigilance=Vigilance.LOW,
        timestamp=at,
    )


async def test_first_stored_point_of_each_minute_is_kept(rig_a):
    times = [T0 + timedelta(seconds=s) for s in (5, 20, 59, 61, 75)]
    await store_points(rig_a.route_id, [_point(rig_a.route_id, t) for t in times[:2]])
    await store_points(rig_a.route_id, [_point(rig_a.route_id, t) for t in times[2:]])
    points = await StatusRoute.find(StatusRoute.id_route == rig_a.route_id).sort(
        +StatusRoute.timestamp
    ).to_list()
    assert [p.keep for p in points] == [True, False, False, True, False]


async def test_keep_is_per_route(rig_a, rig_b):
    await store_points(rig_a.route_id, [_point(rig_a.route_id, T0)])
    await store_points(rig_b.route_id, [_point(rig_b.route_id, T0 + timedelta(seconds=1))])
    assert all(p.keep for p in await StatusRoute.find_all().to_list())


async def test_duplicate_points_are_skipped(rig_a):
    first = await store_points(rig_a.route_id, [_point(rig_a.route_id, T0)])
    again = await store_points(
        rig_a.route_id,
        [_point(rig_a.route_id, T0), _point(rig_a.route_id, T0 + timedelta(seconds=1))],
    )
    assert (len(first), len(again)) == (1, 1)
    assert await StatusRoute.find_all().count() == 2


async def test_ttl_index_expires_only_points_not_kept(mongo_client):
    info = await StatusRoute.get_motor_collection().index_information()
    ttl = info[POINT_TTL]
    assert ttl["key"] == [("timestamp", 1)]
    assert ttl["expireAfterSeconds"] == 7 * 24 * 3600
    assert ttl["partialFilterExpression"] == {"keep": False}
