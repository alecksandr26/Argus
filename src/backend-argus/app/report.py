"""`POST /api/devices/me/report` processing (docs/designs/final-tweaks-contracts.md, C2.2).

A report carries up to `REPORT_MAX_POINTS` GPS points and at most one event. The two parts get
separate results: points go to the truck's *current* active route; the event goes to the route
the truck was on *when it happened* (by time), so a replayed event is never stamped with a route
that started after it.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from beanie import PydanticObjectId
from pydantic import ValidationError
from pymongo.errors import BulkWriteError

from app.constants import ROUTE_LOCK_WAIT
from app.geo import GeoJSONPoint
from app.ids import get_by_id
from app.incidents import (
    FINISHED_ROUTE_STATUSES,
    RouteFinishedError,
    apply_points_to_route,
    ensure_route_open,
    as_utc,
    ingest_alert,
    ingest_driver_ok,
    newest_alert_of_episode,
    route_lock,
    utcnow,
)
from app.models.alert import Alert, AlertAiMetadata
from app.models.common import AlertSource, Delivery, RouteStatus, Severity, Vigilance
from app.models.incident import DriverOk
from app.models.route import Route
from app.models.status_route import StatusRoute
from app.models.truck import Truck
from app.schemas.report import (
    MAX_AGE_S,
    EventResult,
    PointsResult,
    ReportEnvelope,
    ReportEvent,
    ReportPoint,
    ReportResponse,
)

ACTIVE_ROUTE_STATUSES = (RouteStatus.IN_PROGRESS, RouteStatus.IN_PROGRESS_ALERT)
_DUPLICATE_KEY = 11000


def _started_key(route: Route) -> datetime:
    """Newest departure wins; a legacy route with no `actual_departure` falls back to its
    estimated one."""
    return as_utc(route.actual_departure or route.estimated_departure)


async def active_route_for(truck_id: str) -> Optional[Route]:
    """The truck's current active route (`in_progress`/`in_progress_alert`); several (a data
    entry mistake) -> the newest departure."""
    routes = await Route.find(
        Route.id_truck == truck_id,
        {"operative_status": {"$in": [s.value for s in ACTIVE_ROUTE_STATUSES]}},
    ).to_list()
    return max(routes, key=_started_key) if routes else None


async def _points_status_without_route(truck_id: str) -> str:
    """`route_finished` when the truck's most recently started route is completed/cancelled,
    else `no_active_route`."""
    latest = (
        await Route.find(Route.id_truck == truck_id, Route.actual_departure != None)  # noqa: E711
        .sort(-Route.actual_departure)
        .first_or_none()
    )
    if latest is not None and latest.operative_status in FINISHED_ROUTE_STATUSES:
        return "route_finished"
    return "no_active_route"


def _to_ms(value: datetime) -> datetime:
    """Truncated to milliseconds, the resolution Mongo stores (so what we compare in memory is
    what gets stored)."""
    return value.replace(microsecond=value.microsecond // 1000 * 1000)


async def _keep_minutes(route_id: str, timestamps: list[datetime]) -> set[datetime]:
    """UTC minutes of `route_id` that already have a `keep` point."""
    start = min(timestamps).replace(second=0, microsecond=0)
    end = max(timestamps).replace(second=0, microsecond=0) + timedelta(minutes=1)
    kept = await StatusRoute.find(
        StatusRoute.id_route == route_id,
        StatusRoute.keep == True,  # noqa: E712 (Beanie query syntax)
        StatusRoute.timestamp >= start,
        StatusRoute.timestamp < end,
    ).to_list()
    return {as_utc(p.timestamp).replace(second=0, microsecond=0) for p in kept}


async def store_points(
    route_id: str,
    docs: list[StatusRoute],
    *,
    reject_finished: bool = True,
    deadline: Optional[datetime] = None,
) -> list[StatusRoute]:
    """Inserts GPS points of one route, skipping the ones whose `(id_route, timestamp)` is
    already stored (unique index, `insert_many(ordered=False)`). Marks the first stored point of
    each UTC minute `keep` (retention, C3.3), then updates the route state from the stored
    points (C3.5). Returns the points actually stored.

    With `reject_finished` (every device write), the route's status is re-read under the lock
    and `RouteFinishedError` is raised, nothing stored, if it ended since the caller picked it
    (C2.2). A user's manual write to a finished route passes `reject_finished=False`.
    `deadline`: the request's lock deadline (`route_lock`, C2.3 [sync 3])."""
    if not docs:
        return []
    by_time: dict[datetime, StatusRoute] = {}
    for doc in sorted(docs, key=lambda d: as_utc(d.timestamp)):
        by_time.setdefault(as_utc(doc.timestamp), doc)
    docs = list(by_time.values())

    async with route_lock(route_id, deadline=deadline):
        if reject_finished:
            await ensure_route_open(route_id)
        kept_minutes = await _keep_minutes(route_id, list(by_time))
        for doc in docs:
            minute = as_utc(doc.timestamp).replace(second=0, microsecond=0)
            doc.keep = minute not in kept_minutes
            kept_minutes.add(minute)
            if doc.id is None:
                doc.id = PydanticObjectId()  # insert_many doesn't set ids on the documents

        failed: set[int] = set()
        try:
            await StatusRoute.insert_many(docs, ordered=False)
        except BulkWriteError as exc:
            errors = exc.details.get("writeErrors", [])
            if any(e.get("code") != _DUPLICATE_KEY for e in errors):
                raise
            failed = {e["index"] for e in errors}
        stored = [doc for i, doc in enumerate(docs) if i not in failed]
        if stored:
            await apply_points_to_route(route_id, [(d.timestamp, d.vigilance) for d in stored])
    return stored


def _point_doc(route_id: str, point: ReportPoint, received_at: datetime) -> StatusRoute:
    return StatusRoute(
        id_route=route_id,
        current_coordinates=GeoJSONPoint(coordinates=(point.lon, point.lat)),
        current_speed=point.speed,
        odometer=0.0,
        vigilance=Vigilance(point.vigilance),
        location_is_last_known=point.last_known,
        location_age_s=point.location_age_s,
        # Millisecond precision, never rounded to the second (C2.1 [sync 3]): rounding made the
        # unique (id_route, timestamp) index drop distinct points whenever the request latency
        # differed by about a second between two reports. The index now only skips exact
        # duplicates.
        timestamp=_to_ms(received_at - timedelta(seconds=point.age_s)),
        received_at=received_at,
        time_source="relative",
    )


async def _process_points(
    truck: Truck,
    raw_points: list[Any],
    received_at: datetime,
    active: Optional[Route],
    deadline: datetime,
) -> PointsResult:
    if active is None:
        return PointsResult(
            stored=0, route=None, status=await _points_status_without_route(str(truck.id))
        )
    route_id = str(active.id)
    docs: list[StatusRoute] = []
    for raw in raw_points:
        try:
            point = ReportPoint.model_validate(raw)
        except ValidationError:
            continue  # an invalid point is skipped, never a 422 (C2.1)
        docs.append(_point_doc(route_id, point, received_at))
    try:
        stored = await store_points(route_id, docs, deadline=deadline)
    except RouteFinishedError:
        # Stopped/completed between `active_route_for` and the lock (C2.2).
        return PointsResult(stored=0, route=None, status="route_finished")
    return PointsResult(stored=len(stored), route=route_id, status="ok")


def _rejected(reason: str) -> EventResult:
    return EventResult(status="rejected", reason=reason, id_alert=None)


async def _belongs_to_other_truck(truck_id: str, client_event_id: str) -> bool:
    """C2.2 rule 3: the id is already stored on an alert or driver-OK of another truck's
    route."""
    route_ids = {a.id_route for a in await Alert.find(Alert.client_event_id == client_event_id).to_list()}
    route_ids |= {
        o.id_route for o in await DriverOk.find(DriverOk.client_event_id == client_event_id).to_list()
    }
    for route_id in route_ids:
        route = await get_by_id(Route, route_id)
        if route is not None and route.id_truck != truck_id:
            return True
    return False


async def route_at(truck_id: str, occurred_at: datetime) -> Optional[Route]:
    """C2.2 rule 4: the truck's route with `actual_departure <= occurred_at` and
    (`actual_arrival` null or `occurred_at <= actual_arrival`); several -> newest departure."""
    return (
        await Route.find(
            Route.id_truck == truck_id,
            Route.actual_departure <= occurred_at,
            {"$or": [{"actual_arrival": None}, {"actual_arrival": {"$gte": occurred_at}}]},
        )
        .sort(-Route.actual_departure)
        .first_or_none()
    )


async def _no_route_reason(truck_id: str, occurred_at: datetime) -> str:
    """C2.2 rule 4 [sync 2], when no route contains `occurred_at`: the truck's newest route that
    had departed by then decides. Completed/cancelled -> `route_finished` (the event happened
    after that route ended, e.g. an OK sent after a stop); none at all -> `no_route` (the truck
    hadn't started any route yet)."""
    latest = (
        await Route.find(Route.id_truck == truck_id, Route.actual_departure <= occurred_at)
        .sort(-Route.actual_departure)
        .first_or_none()
    )
    if latest is not None and latest.operative_status in FINISHED_ROUTE_STATUSES:
        return "route_finished"
    return "no_route"


def _alert_from_event(route_id: str, event: ReportEvent, occurred_at: datetime, received_at: datetime) -> Alert:
    position = event.position
    ai = event.ai_metadata
    return Alert(
        id_route=route_id,
        alert_type=event.alert_type,
        severity_level=Severity(event.severity),
        source=AlertSource.FUSION,
        ai_metadata=(
            AlertAiMetadata(model=ai.model, scores=ai.scores.model_dump() if ai.scores else None)
            if ai is not None
            else None
        ),
        episode=event.episode,
        delivery=Delivery(event.delivery),
        queued_seconds=event.queued_seconds,
        coordinates=GeoJSONPoint(coordinates=(position.lon, position.lat)) if position else None,
        location_is_last_known=position.last_known if position else False,
        location_age_s=position.location_age_s if position else None,
        speed_at_event=event.speed,
        timestamp=occurred_at,
        received_at=received_at,
        time_source="relative",
        client_event_id=event.client_event_id,
    )


async def _process_event(
    truck: Truck, raw_event: dict[str, Any], received_at: datetime, deadline: datetime
) -> EventResult:
    try:
        event = ReportEvent.model_validate(raw_event)
    except ValidationError:
        return _rejected("invalid")
    if event.age_s > MAX_AGE_S:
        return _rejected("too_old")
    occurred_at = _to_ms(received_at - timedelta(seconds=event.age_s))
    truck_id = str(truck.id)
    if await _belongs_to_other_truck(truck_id, event.client_event_id):
        return _rejected("wrong_truck")
    route = await route_at(truck_id, occurred_at)
    if route is None:
        return _rejected(await _no_route_reason(truck_id, occurred_at))
    if route.operative_status in FINISHED_ROUTE_STATUSES:
        return _rejected("route_finished")
    route_id = str(route.id)

    try:
        if event.kind == "alert":
            alert, created = await ingest_alert(
                _alert_from_event(route_id, event, occurred_at, received_at), deadline=deadline
            )
            return EventResult(
                status="created" if created else "duplicate", id_alert=str(alert.id)
            )
        created = await ingest_driver_ok(
            DriverOk(
                id_route=route_id,
                episode=event.episode,
                client_event_id=event.client_event_id,
                occurred_at=occurred_at,
                received_at=received_at,
                delivery=event.delivery,
                queued_seconds=event.queued_seconds,
            ),
            deadline=deadline,
        )
    except RouteFinishedError:
        # Stopped/completed between `route_at` and the lock (C2.2).
        return _rejected("route_finished")
    newest = await newest_alert_of_episode(route_id, event.episode)
    return EventResult(
        status="created" if created else "duplicate",
        id_alert=str(newest.id) if newest is not None else None,
    )


async def process_report(truck: Truck, envelope: ReportEnvelope) -> ReportResponse:
    received_at = utcnow()
    # One lock deadline for the whole request (C2.3 [sync 3]): the points and the event may each
    # take the route lock, and together they still wait at most ROUTE_LOCK_WAIT before the 503,
    # inside the ESP32's 8 s attempt timeout and the 10 s Lambda timeout.
    deadline = received_at + ROUTE_LOCK_WAIT
    active = await active_route_for(str(truck.id))
    points = await _process_points(truck, envelope.points, received_at, active, deadline)
    event = (
        await _process_event(truck, envelope.event, received_at, deadline)
        if envelope.event is not None
        else None
    )
    # `points.route` is the active route, or null when there is none -- including when it was
    # stopped while this report was being stored.
    return ReportResponse(points=points, event=event, active_route=points.route)



