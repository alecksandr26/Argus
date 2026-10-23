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
from scripts.migrate_final_tweaks import MIGRATION_USER, migrate
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
    assert closed.closed_by == MIGRATION_USER
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
