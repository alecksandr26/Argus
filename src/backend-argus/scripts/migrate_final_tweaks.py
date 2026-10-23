"""One-off, idempotent migration of an existing database to the final-tweaks data model
(docs/designs/final-tweaks-contracts.md). Run it once after deploying the new backend:

    python -m scripts.migrate_final_tweaks --dry-run               # only count what would change
    python -m scripts.migrate_final_tweaks                         # apply
    python -m scripts.migrate_final_tweaks --close-ended-routes    # apply, and close the
                                                                   # incidents it creates on
                                                                   # routes that already ended

against whatever `MONGO_URI`/`MONGO_DB` the environment points at (from `src/backend-argus/`).
Every step is safe to run again; a second run changes nothing.

Steps, in order:
1. `resolved_at` -> `driver_ok_at` on alerts (the device "resolve" now means "driver OK").
2. `actual_departure`/`actual_arrival` on started routes that predate them (events are matched
   to routes by these times): in progress or completed with no departure -> the estimated
   departure; completed with no arrival -> its last update.
3. Incidents for historical medium/critical alerts (grouped exactly like new ones), the route
   `incident_state` they imply, and closing the incidents whose alerts were all reviewed
   already (a guardian looked at them before incidents existed). The others stay open, and
   `/api/live` lists every open incident, ended routes' included: the report's
   `open_incidents_on_ended_routes` says how many of those the migration creates on
   completed/cancelled routes (the dry run counts them too). `--close-ended-routes` (off by
   default; the user decides) closes exactly those, with `closed_by: null` and a migration
   note, so Live operations doesn't start full of old history. Pass it on the first real run:
   a later run has nothing left to group, so it closes nothing.
4. GPS points: `keep` for the first point of each route-minute (retention), and duplicates of
   the same `(id_route, timestamp)` removed (keeping the first stored). Destructive once the
   TTL index exists: MongoDB's TTL pass then deletes every point with `keep: false` older than
   7 days, i.e. GPS history older than a week keeps one point per route per minute.
5. Alerts sharing an `(id_route, client_event_id)`: the later copies lose the id (kept, not
   deleted) so the unique index can build.
6. Drops the old plain `(id_route, client_event_id)` alerts index and creates the unique and
   TTL indexes of `app/indexes.py`, strictly (any failure stops the script).
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from datetime import datetime, timezone

from pymongo.errors import OperationFailure

from app.database import init_db
from app.ids import to_object_id
from app.constants import INCIDENT_GROUP_WINDOW
from app.incidents import (
    FINISHED_ROUTE_STATUSES,
    GROUPED_SEVERITIES,
    as_utc,
    group_alert,
    lower_route_state_after_close,
    raise_route_state,
)
from app.indexes import ensure_indexes
from app.models.alert import Alert
from app.models.common import RouteStatus
from app.models.incident import Incident
from app.models.route import Route
from app.models.status_route import StatusRoute

MIGRATION_USER = "migration"
MIGRATION_NOTE = "Closed by the final-tweaks migration: every alert was reviewed before incidents existed."
ENDED_ROUTE_NOTE = "Closed by the final-tweaks migration: route had already ended"
OLD_ALERT_EVENT_INDEX = "id_route_1_client_event_id_1"


async def migrate_resolved_at(dry_run: bool = False) -> int:
    collection = Alert.get_motor_collection()
    query = {"resolved_at": {"$ne": None}}
    count = 0
    async for doc in collection.find(query, {"resolved_at": 1, "driver_ok_at": 1}):
        count += 1
        if dry_run:
            continue
        update: dict = {"$unset": {"resolved_at": ""}}
        if doc.get("driver_ok_at") is None:
            update["$set"] = {"driver_ok_at": doc["resolved_at"]}
        await collection.update_one({"_id": doc["_id"]}, update)
    if not dry_run:
        # Rows with an explicit null left over from the old model.
        await collection.update_many({"resolved_at": None}, {"$unset": {"resolved_at": ""}})
    return count


async def backfill_route_times(dry_run: bool = False) -> int:
    started = [RouteStatus.IN_PROGRESS, RouteStatus.IN_PROGRESS_ALERT, RouteStatus.COMPLETED]
    count = 0
    for route in await Route.find({"operative_status": {"$in": [s.value for s in started]}}).to_list():
        fields: dict = {}
        if route.actual_departure is None:
            fields["actual_departure"] = as_utc(route.estimated_departure)
        if route.operative_status == RouteStatus.COMPLETED and route.actual_arrival is None:
            fields["actual_arrival"] = as_utc(route.updated_at)
        if fields:
            count += 1
            if not dry_run:
                await Route.get_motor_collection().update_one({"_id": route.id}, {"$set": fields})
    return count


async def _ended_route_ids() -> set[str]:
    finished = [st.value for st in FINISHED_ROUTE_STATUSES]
    return {str(r.id) for r in await Route.find({"operative_status": {"$in": finished}}).to_list()}


async def _simulate_grouping(alerts: list[Alert]) -> list[dict]:
    """Dry run: groups `alerts` (timestamp ascending) in memory exactly like `group_alert`
    would, against the open incidents already stored. Returns the incidents the migration
    would create, each as `{"id_route", "reviewed": [bool per alert]}`, plus the existing ones
    it would add alerts to (`"existing": True`)."""
    by_route: dict[str, list[dict]] = defaultdict(list)
    for incident in await Incident.find(Incident.closed_at == None).to_list():  # noqa: E711
        members = await Alert.find(Alert.id_incident == str(incident.id)).to_list()
        by_route[incident.id_route].append(
            {
                "id_route": incident.id_route,
                "first": as_utc(incident.first_alert_at),
                "last": as_utc(incident.last_alert_at),
                "reviewed": [a.reviewed_by_operator for a in members],
                "existing": True,
                "touched": False,
            }
        )
    for alert in alerts:
        t = as_utc(alert.timestamp)
        candidates = [
            i
            for i in by_route[alert.id_route]
            if i["first"] - INCIDENT_GROUP_WINDOW < t < i["last"] + INCIDENT_GROUP_WINDOW
        ]
        if candidates:
            incident = max(candidates, key=lambda i: i["last"])
        else:
            incident = {
                "id_route": alert.id_route,
                "first": t,
                "last": t,
                "reviewed": [],
                "existing": False,
                "touched": False,
            }
            by_route[alert.id_route].append(incident)
        incident["first"] = min(incident["first"], t)
        incident["last"] = max(incident["last"], t)
        incident["reviewed"].append(alert.reviewed_by_operator)
        incident["touched"] = True
    return [i for incidents in by_route.values() for i in incidents if i["touched"]]


async def _close(incident_id: str, closed_by, note: str, now: datetime) -> None:
    await Incident.get_motor_collection().update_one(
        {"_id": to_object_id(incident_id)},
        {"$set": {"closed_at": now, "closed_by": closed_by, "close_note": note}},
    )
    await Alert.get_motor_collection().update_many(
        {"id_incident": incident_id}, {"$set": {"closed_at": now}}
    )


async def backfill_incidents(dry_run: bool = False, close_ended_routes: bool = False) -> dict:
    """Groups historical medium/critical alerts into incidents. Closes the incidents whose
    alerts were all reviewed, and (with `close_ended_routes`) the other new ones on completed/
    cancelled routes; then sets each route's `incident_state` from its open incidents.

    Returns `alerts_grouped`, `open_incidents_on_ended_routes` (incidents this run creates, or
    would create, that stay open on a completed/cancelled route unless `close_ended_routes`),
    and `ended_route_incidents_closed`."""
    alerts = (
        await Alert.find(
            {
                "id_incident": None,
                "severity_level": {"$in": [s.value for s in GROUPED_SEVERITIES]},
            }
        )
        .sort(+Alert.timestamp)
        .to_list()
    )
    ended = await _ended_route_ids()
    if dry_run:
        simulated = await _simulate_grouping(alerts)
        on_ended = sum(
            1
            for i in simulated
            if not i["existing"] and i["id_route"] in ended and not all(i["reviewed"])
        )
        return {
            "alerts_grouped": len(alerts),
            "open_incidents_on_ended_routes": on_ended,
            "ended_route_incidents_closed": on_ended if close_ended_routes else 0,
        }

    already_open = {
        str(i.id) for i in await Incident.find(Incident.closed_at == None).to_list()  # noqa: E711
    }
    touched: set[str] = set()
    for alert in alerts:
        incident = await group_alert(alert)
        if incident is not None:
            touched.add(str(incident.id))
        await raise_route_state(alert.id_route, alert.severity_level, alert.timestamp)

    now = datetime.now(timezone.utc)
    routes_touched: set[str] = set()
    on_ended = 0
    closed_ended = 0
    for incident_id in touched:
        incident = await Incident.get(incident_id)
        if incident is None or incident.closed_at is not None:
            continue
        routes_touched.add(incident.id_route)
        members = await Alert.find(Alert.id_incident == incident_id).to_list()
        if members and all(a.reviewed_by_operator for a in members):
            await _close(incident_id, MIGRATION_USER, MIGRATION_NOTE, now)
            continue
        if incident_id in already_open or incident.id_route not in ended:
            continue
        on_ended += 1
        if close_ended_routes:
            await _close(incident_id, None, ENDED_ROUTE_NOTE, now)
            closed_ended += 1
    for route_id in routes_touched:
        await lower_route_state_after_close(route_id)
    return {
        "alerts_grouped": len(alerts),
        "open_incidents_on_ended_routes": on_ended,
        "ended_route_incidents_closed": closed_ended,
    }


async def backfill_points(dry_run: bool = False) -> tuple[int, int]:
    """Returns `(points given a keep flag, duplicates removed)`."""
    collection = StatusRoute.get_motor_collection()
    flagged = 0
    removed = 0
    route_ids = await collection.distinct("id_route")
    for route_id in route_ids:
        seen_times: set[datetime] = set()
        kept_minutes: set[datetime] = set()
        cursor = collection.find({"id_route": route_id}, {"timestamp": 1, "keep": 1}).sort(
            [("timestamp", 1), ("_id", 1)]
        )
        async for doc in cursor:
            t = as_utc(doc["timestamp"])
            if t in seen_times:
                removed += 1
                if not dry_run:
                    await collection.delete_one({"_id": doc["_id"]})
                continue
            seen_times.add(t)
            minute = t.replace(second=0, microsecond=0)
            if "keep" in doc:
                if doc["keep"]:
                    kept_minutes.add(minute)
                continue
            keep = minute not in kept_minutes
            kept_minutes.add(minute)
            flagged += 1
            if not dry_run:
                await collection.update_one({"_id": doc["_id"]}, {"$set": {"keep": keep}})
    return flagged, removed


async def clear_duplicate_alert_event_ids(dry_run: bool = False) -> int:
    collection = Alert.get_motor_collection()
    groups: dict[tuple[str, str], list] = defaultdict(list)
    async for doc in collection.find(
        {"client_event_id": {"$type": "string"}}, {"id_route": 1, "client_event_id": 1}
    ).sort("_id", 1):
        groups[(doc["id_route"], doc["client_event_id"])].append(doc["_id"])
    count = 0
    for ids in groups.values():
        for duplicate in ids[1:]:
            count += 1
            if not dry_run:
                await collection.update_one({"_id": duplicate}, {"$set": {"client_event_id": None}})
    return count


async def replace_indexes(database) -> None:
    try:
        await database["alerts"].drop_index(OLD_ALERT_EVENT_INDEX)
    except OperationFailure:
        pass  # already dropped (or never existed)
    await ensure_indexes(database, strict=True)


async def migrate(*, client=None, dry_run: bool = False, close_ended_routes: bool = False) -> dict:
    from app.config import settings

    client = await init_db(client=client)
    report = {
        "resolved_at_moved": await migrate_resolved_at(dry_run),
        "routes_with_times_backfilled": await backfill_route_times(dry_run),
    }
    report.update(await backfill_incidents(dry_run, close_ended_routes))
    report["points_flagged"], report["duplicate_points_removed"] = await backfill_points(dry_run)
    report["duplicate_alert_ids_cleared"] = await clear_duplicate_alert_event_ids(dry_run)
    if not dry_run:
        await replace_indexes(client[settings.mongo_db])
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="only count what would change")
    parser.add_argument(
        "--close-ended-routes",
        action="store_true",
        help=(
            "close the incidents the migration creates on completed/cancelled routes "
            "(closed_by null, with a migration note) instead of leaving them open in Live "
            "operations; off by default"
        ),
    )
    args = parser.parse_args()
    result = asyncio.run(
        migrate(dry_run=args.dry_run, close_ended_routes=args.close_ended_routes)
    )
    print("Dry run (nothing written):" if args.dry_run else "Migrated:")
    for key, value in result.items():
        print(f"  {key}: {value}")
