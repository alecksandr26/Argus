"""`scripts/migrate_final_tweaks.py`: an old database becomes the final-tweaks data model, and a
second run changes nothing."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config import settings
from app.indexes import ALERT_EVENT_UNIQUE, POINT_TIME_UNIQUE, POINT_TTL
from app.models.alert import Alert
from app.models.common import RouteStatus
from app.models.incident import Incident
from app.models.route import Route
from app.models.status_route import StatusRoute
from scripts.migrate_final_tweaks import ENDED_ROUTE_NOTE, migrate
from scripts.migrate_final_tweaks import MIGRATION_NOTE as MIGRATION_NOTE_ALL_REVIEWED
from tests.conftest import make_rig

T0 = datetime(2030, 1, 1, 8, tzinfo=timezone.utc)


_ids = iter(range(1000))


def _old_alert(route_id: str, at: datetime, severity: str = "critical", **extra) -> dict:
    """An alert as the old backend stored it (resolved_at, no incident fields). Each gets a
    distinct client_event_id unless the test sets one: mongomock checks existing rows against a
    new unique index without its partialFilterExpression (real MongoDB skips null/missing ids),
    so rows without an id would make the index build fail here only."""
    doc = {
        "client_event_id": f"old-{next(_ids)}",
        "id_route": route_id,
        "alert_type": "Drowsy",
        "severity_level": severity,
        "source": "fusion",
        "ai_metadata": {"scores": {"not_drowsy": 0.1, "drowsy": 0.9}},
        "grip_status": "bad",
        "coordinates": {"type": "Point", "coordinates": [-103.3, 20.6]},
        "speed_at_event": 50.0,
        "timestamp": at,
        "received_at": at,
        "time_source": "gps",
        "location_source": "gps",
        "reviewed_by_operator": False,
        "operator_notes": "",
        "deescalation_reason": "",
    }
    doc.update(extra)
    return doc


def _old_point(route_id: str, at: datetime) -> dict:
    return {
        "id_route": route_id,
        "current_coordinates": {"type": "Point", "coordinates": [-103.3, 20.6]},
        "current_speed": 50.0,
        "odometer": 10.0,
        "vigilance": "low",
        "timestamp": at,
        "received_at": at,
        "time_source": "gps",
        "location_source": "gps",
    }


async def _seed_old_database(mongo_client):
    db = mongo_client[settings.mongo_db]
    # Drop what init_db created, to start from the old shape.
    for name in (ALERT_EVENT_UNIQUE,):
        await db["alerts"].drop_index(name)
    for name in (POINT_TIME_UNIQUE, POINT_TTL):
        await db["status_routes"].drop_index(name)
    await db["alerts"].create_index([("id_route", 1), ("client_event_id", 1)])

    active = await make_rig("MIG-1", departed_ago=timedelta(hours=2))
    done = await make_rig("MIG-2", route_status=RouteStatus.COMPLETED)
    await Route.get_motor_collection().update_many({}, {"$set": {"actual_departure": None, "actual_arrival": None}})

    alerts = db["alerts"]
    await alerts.insert_many(
        [
            _old_alert(active.route_id, T0, "medium", resolved_at=T0 + timedelta(minutes=2)),
            _old_alert(active.route_id, T0 + timedelta(minutes=5)),
            _old_alert(active.route_id, T0 + timedelta(hours=2), client_event_id="dup"),
            _old_alert(active.route_id, T0 + timedelta(hours=2, seconds=1), client_event_id="dup"),
            _old_alert(done.route_id, T0, "medium", reviewed_by_operator=True),
            _old_alert(done.route_id, T0, "low"),
            _old_alert(active.route_id, T0, "critical", source="panic_button", ai_metadata=None, grip_status=None),
        ]
    )
    points = db["status_routes"]
    await points.insert_many(
        [
            _old_point(active.route_id, T0 + timedelta(seconds=1)),
            _old_point(active.route_id, T0 + timedelta(seconds=1)),  # duplicate
            _old_point(active.route_id, T0 + timedelta(seconds=30)),
            _old_point(active.route_id, T0 + timedelta(seconds=61)),
        ]
    )
    return active, done


async def test_migration(mongo_client):
    active, done = await _seed_old_database(mongo_client)
    dry = await migrate(client=mongo_client, dry_run=True)
    assert dry["resolved_at_moved"] == 1
    assert await Incident.find_all().count() == 0  # a dry run writes nothing

    report = await migrate(client=mongo_client)
    assert report == {
        "resolved_at_moved": 1,
        "routes_with_times_backfilled": 2,
        "alerts_grouped": 6,  # medium/critical, the panic row included; the low one not
        # The finished route's only incident was all reviewed, so the migration closed it.
        "open_incidents_on_ended_routes": 0,
        "ended_route_incidents_closed": 0,
        "points_flagged": 3,
        "duplicate_points_removed": 1,
        "duplicate_alert_ids_cleared": 1,
    }

    # 1. resolved_at -> driver_ok_at
    raw = await Alert.get_motor_collection().find_one({"severity_level": "medium", "id_route": active.route_id})
    assert "resolved_at" not in raw
    assert raw["driver_ok_at"] is not None

    # 2. route times
    route = await Route.get(active.route_id)
    finished = await Route.get(done.route_id)
    assert route.actual_departure is not None and route.actual_arrival is None
    assert finished.actual_departure is not None and finished.actual_arrival is not None

    # 3. incidents: two on the active route (2 h apart), one closed on the finished route
    open_active = await Incident.find(Incident.id_route == active.route_id).to_list()
    assert len(open_active) == 2
    assert all(i.closed_at is None for i in open_active)
    closed = await Incident.find_one(Incident.id_route == done.route_id)
    # Closed by the system (C3.4 [sync 3]): no user id, and the note says why.
    assert closed.closed_by is None
    assert closed.close_note == MIGRATION_NOTE_ALL_REVIEWED
    assert route.incident_state == "critical"
    assert finished.incident_state == "fine"
    low = await Alert.find_one(Alert.severity_level == "low")
    assert low.id_incident is None

    # 4. points
    points = await StatusRoute.find_all().sort(+StatusRoute.timestamp).to_list()
    assert [p.keep for p in points] == [True, False, True]

    # 5. + 6. duplicate ids cleared and the indexes in place
    assert await Alert.find(Alert.client_event_id == "dup").count() == 1
    alert_indexes = await Alert.get_motor_collection().index_information()
    point_indexes = await StatusRoute.get_motor_collection().index_information()
    assert ALERT_EVENT_UNIQUE in alert_indexes
    assert "id_route_1_client_event_id_1" not in alert_indexes
    assert {POINT_TIME_UNIQUE, POINT_TTL} <= set(point_indexes)

    # A second run changes nothing.
    again = await migrate(client=mongo_client)
    assert set(again.values()) == {0}
    assert await Incident.find_all().count() == 3


async def _seed_ended_routes():
    """Unreviewed history on routes that already ended (plus an active route): what would fill
    Live operations with old open incidents after the migration."""
    active = await make_rig("END-0", departed_ago=timedelta(hours=2))
    completed = await make_rig("END-1", route_status=RouteStatus.COMPLETED)
    cancelled = await make_rig("END-2", route_status=RouteStatus.CANCELLED)
    await Alert.get_motor_collection().insert_many(
        [
            # Two incidents on the completed route (2 h apart).
            _old_alert(completed.route_id, T0, "medium"),
            _old_alert(completed.route_id, T0 + timedelta(minutes=5)),
            _old_alert(completed.route_id, T0 + timedelta(hours=2)),
            # One on the cancelled route, one of its alerts reviewed (not all: stays open).
            _old_alert(cancelled.route_id, T0, "medium", reviewed_by_operator=True),
            _old_alert(cancelled.route_id, T0 + timedelta(minutes=1)),
            # All reviewed: closed by the migration as before, whatever the flag.
            _old_alert(cancelled.route_id, T0 + timedelta(hours=5), reviewed_by_operator=True),
            # On the active route: stays open in any case.
            _old_alert(active.route_id, T0),
        ]
    )
    return active, completed, cancelled


async def test_dry_run_counts_open_incidents_on_ended_routes(mongo_client):
    await _seed_ended_routes()
    dry = await migrate(client=mongo_client, dry_run=True)
    assert dry["alerts_grouped"] == 7
    assert dry["open_incidents_on_ended_routes"] == 3
    assert dry["ended_route_incidents_closed"] == 0
    flagged = await migrate(client=mongo_client, dry_run=True, close_ended_routes=True)
    assert flagged["ended_route_incidents_closed"] == 3
    assert await Incident.find_all().count() == 0  # a dry run writes nothing


async def test_default_leaves_ended_route_incidents_open(mongo_client):
    active, completed, cancelled = await _seed_ended_routes()
    report = await migrate(client=mongo_client)
    assert report["open_incidents_on_ended_routes"] == 3
    assert report["ended_route_incidents_closed"] == 0
    open_incidents = await Incident.find(Incident.closed_at == None).to_list()  # noqa: E711
    assert sorted(i.id_route for i in open_incidents) == sorted(
        [completed.route_id, completed.route_id, cancelled.route_id, active.route_id]
    )
    assert (await Route.get(completed.route_id)).incident_state == "critical"


async def test_close_ended_routes_closes_them_with_a_note(mongo_client):
    active, completed, cancelled = await _seed_ended_routes()
    report = await migrate(client=mongo_client, close_ended_routes=True)
    assert report["open_incidents_on_ended_routes"] == 3
    assert report["ended_route_incidents_closed"] == 3

    ended = await Incident.find(
        {"id_route": {"$in": [completed.route_id, cancelled.route_id]}}
    ).to_list()
    # Both migration closures look the same: closed_by null, told apart only by the note.
    by_note = sorted((i.close_note, i.closed_by) for i in ended)
    assert by_note == sorted(
        [(ENDED_ROUTE_NOTE, None)] * 3 + [(MIGRATION_NOTE_ALL_REVIEWED, None)]
    )
    assert all(i.closed_at is not None for i in ended)
    closed_alerts = await Alert.find(
        {"id_route": {"$in": [completed.route_id, cancelled.route_id]}}
    ).to_list()
    assert all(a.closed_at is not None for a in closed_alerts)
    # Nothing was reviewed on the guardian's behalf.
    assert sum(a.reviewed_by_operator for a in closed_alerts) == 2
    # Closing lowers the ended routes' state; the active route keeps its open incident.
    assert (await Route.get(completed.route_id)).incident_state == "fine"
    assert (await Route.get(cancelled.route_id)).incident_state == "fine"
    still_open = await Incident.find(Incident.closed_at == None).to_list()  # noqa: E711
    assert [i.id_route for i in still_open] == [active.route_id]
    assert (await Route.get(active.route_id)).incident_state == "critical"

    again = await migrate(client=mongo_client, close_ended_routes=True)
    assert set(again.values()) == {0}

