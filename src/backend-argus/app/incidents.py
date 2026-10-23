"""Incidents and route state: the core rules of docs/designs/final-tweaks-contracts.md C3.4/C3.5.

Everything that changes an incident, a route's `incident_state`, or an alert's incident fields
runs under that route's lock (`route_lock`), so two requests (possibly in two Lambda containers)
can't group the same alert twice or overwrite each other's route state. Route fields are written
with targeted `$set`s, never a whole-document save, so a concurrent `PUT /api/routes/{id}` can't
undo them (and they can't undo it).
"""
from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator, Iterable, Optional

from beanie.operators import In
from fastapi import HTTPException, status
from pymongo.errors import DuplicateKeyError

from app.constants import (
    INCIDENT_GROUP_WINDOW,
    OK_MAX_GAP,
    OLD_INCIDENT_AFTER,
    ROUTE_LOCK_LEASE,
    ROUTE_LOCK_WAIT,
    ROUTE_STATE_CLEAR_AFTER,
)
from app.ids import get_by_id, to_object_id
from app.models.alert import Alert
from app.models.common import Delivery, IncidentState, RouteStatus, Severity, Vigilance
from app.models.driver import Driver
from app.models.incident import DriverOk, Incident
from app.models.route import Route
from app.models.truck import Truck

FINISHED_ROUTE_STATUSES = (RouteStatus.COMPLETED, RouteStatus.CANCELLED)
GROUPED_SEVERITIES = (Severity.MEDIUM, Severity.CRITICAL)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: Optional[datetime]) -> Optional[datetime]:
    """Mongo (and mongomock) hand datetimes back naive; they're UTC."""
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


# --- Per-route lock ---------------------------------------------------------------------------


@asynccontextmanager
async def route_lock(route_id: str) -> AsyncIterator[None]:
    """Serializes grouping/route-state work on one route. Taken with an atomic
    findOneAndUpdate on the Route document (C3.4), held at most `ROUTE_LOCK_LEASE` (a crashed
    holder frees it then). Waits up to `ROUTE_LOCK_WAIT`, then answers 503 so the device retries.
    A route id that doesn't exist (or is malformed) gets no lock: there's nothing to protect."""
    oid = to_object_id(route_id)
    collection = Route.get_motor_collection()
    if oid is None or await collection.count_documents({"_id": oid}, limit=1) == 0:
        yield
        return
    token = uuid.uuid4().hex
    deadline = time.monotonic() + ROUTE_LOCK_WAIT.total_seconds()
    delay = 0.01
    while True:
        now = utcnow()
        taken = await collection.find_one_and_update(
            {"_id": oid, "$or": [{"lock_until": None}, {"lock_until": {"$lt": now}}]},
            {"$set": {"lock_token": token, "lock_until": now + ROUTE_LOCK_LEASE}},
        )
        if taken is not None:
            break
        if time.monotonic() > deadline:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Route is busy, try again")
        await asyncio.sleep(delay)
        delay = min(delay * 2, 0.2)
    try:
        yield
    finally:
        await collection.update_one(
            {"_id": oid, "lock_token": token}, {"$set": {"lock_token": None, "lock_until": None}}
        )


async def _set_route(route_id: str, fields: dict) -> None:
    oid = to_object_id(route_id)
    if oid is not None:
        await Route.get_motor_collection().update_one({"_id": oid}, {"$set": fields})


# --- Incident grouping (C3.4) ----------------------------------------------------------------


async def recompute_incident(incident_id: str) -> Optional[Incident]:
    """Recomputes an incident's aggregate fields from its alerts. An incident left with no
    alerts (they were all deleted) is deleted too. Call under the route's lock."""
    incident = await get_by_id(Incident, incident_id)
    if incident is None:
        return None
    alerts = await Alert.find(Alert.id_incident == str(incident.id)).sort(+Alert.timestamp).to_list()
    if not alerts:
        await incident.delete()
        return None
    rank = max(a.severity_level.rank for a in alerts)
    severity = Severity.CRITICAL if rank >= Severity.CRITICAL.rank else Severity.MEDIUM
    newest = alerts[-1]
    fields = {
        "severity": severity.value,
        "first_alert_at": as_utc(alerts[0].timestamp),
        "last_alert_at": as_utc(newest.timestamp),
        "alert_count": len(alerts),
        "has_replayed": any(a.delivery == Delivery.DLQ for a in alerts),
        "driver_ok_at": as_utc(newest.driver_ok_at),
    }
    await Incident.get_motor_collection().update_one({"_id": incident.id}, {"$set": fields})
    return await get_by_id(Incident, incident.id)


async def group_alert(alert: Alert) -> Optional[Incident]:
    """Puts a stored medium/critical `alert` into its route's open incident whose window
    (`first_alert_at - 15 min` .. `last_alert_at + 15 min`, exclusive) contains the alert's time
    -- the newest such incident if several -- or into a new one. A closed incident never gets new
    alerts. `low` alerts get no incident. Call under the route's lock."""
    if alert.severity_level not in GROUPED_SEVERITIES:
        return None
    t = as_utc(alert.timestamp)
    incident = (
        await Incident.find(
            Incident.id_route == alert.id_route,
            Incident.closed_at == None,  # noqa: E711 (Beanie query syntax)
            Incident.first_alert_at < t + INCIDENT_GROUP_WINDOW,
            Incident.last_alert_at > t - INCIDENT_GROUP_WINDOW,
        )
        .sort(-Incident.last_alert_at)
        .first_or_none()
    )
    if incident is None:
        incident = Incident(
            id_route=alert.id_route,
            severity=alert.severity_level,
            first_alert_at=t,
            last_alert_at=t,
        )
        await incident.insert()
    await Alert.get_motor_collection().update_one(
        {"_id": alert.id}, {"$set": {"id_incident": str(incident.id)}}
    )
    alert.id_incident = str(incident.id)
    return await recompute_incident(str(incident.id))


# --- Route incident_state (C3.5) -------------------------------------------------------------


async def raise_route_state(route_id: str, severity: Severity, occurred_at: datetime) -> None:
    """A new medium/critical alert: `incident_state = max(state, severity)`,
    `last_not_ok_at = max(...)`, and the run of OK points restarts. Call under the lock."""
    if severity not in GROUPED_SEVERITIES:
        return
    route = await get_by_id(Route, route_id)
    if route is None:
        return
    new_state = IncidentState.from_severity(severity)
    if route.incident_state.rank > new_state.rank:
        new_state = route.incident_state
    occurred_at = as_utc(occurred_at)
    last = as_utc(route.last_not_ok_at)
    await _set_route(
        route_id,
        {
            "incident_state": new_state.value,
            "last_not_ok_at": occurred_at if last is None or occurred_at > last else last,
            "ok_since": None,
        },
    )


async def apply_points_to_route(
    route_id: str, points: Iterable[tuple[datetime, Vigilance]]
) -> None:
    """Stored GPS points, processed in time order. A run of `low` points with no gap over
    `OK_MAX_GAP` lasting `ROUTE_STATE_CLEAR_AFTER` clears the state to `fine`. Any other
    vigilance breaks the run; `medium`/`critical` also move `last_not_ok_at`. Points never raise
    the state. A point older than the newest OK point already counted can't extend the run
    (conservative: a late point never clears anything). Call under the route's lock."""
    route = await get_by_id(Route, route_id)
    if route is None:
        return
    ok_since = as_utc(route.ok_since)
    last_ok = as_utc(route.last_ok_point_at)
    last_not_ok = as_utc(route.last_not_ok_at)
    state = route.incident_state
    for t, vigilance in sorted(((as_utc(t), Vigilance(v)) for t, v in points), key=lambda p: p[0]):
        if vigilance == Vigilance.LOW:
            if last_ok is not None and t <= last_ok:
                continue
            if ok_since is None or last_ok is None or t - last_ok > OK_MAX_GAP:
                ok_since = t
            last_ok = t
        else:
            if ok_since is not None and t >= ok_since:
                ok_since = None
            if vigilance in (Vigilance.MEDIUM, Vigilance.CRITICAL):
                if last_not_ok is None or t > last_not_ok:
                    last_not_ok = t
        if (
            state != IncidentState.FINE
            and ok_since is not None
            and last_ok - ok_since >= ROUTE_STATE_CLEAR_AFTER
        ):
            state = IncidentState.FINE
    await _set_route(
        route_id,
        {
            "ok_since": ok_since,
            "last_ok_point_at": last_ok,
            "last_not_ok_at": last_not_ok,
            "incident_state": state.value,
        },
    )


async def lower_route_state_after_close(route_id: str) -> None:
    """Closing can only lower the state: `min(state, max severity of the route's remaining open
    incidents, or fine)`. Call under the route's lock."""
    route = await get_by_id(Route, route_id)
    if route is None:
        return
    open_incidents = await Incident.find(
        Incident.id_route == route_id, Incident.closed_at == None  # noqa: E711
    ).to_list()
    ceiling = IncidentState.FINE
    for incident in open_incidents:
        candidate = IncidentState.from_severity(incident.severity)
        if candidate.rank > ceiling.rank:
            ceiling = candidate
    if ceiling.rank < route.incident_state.rank:
        await _set_route(route_id, {"incident_state": ceiling.value})


# --- Ingest helpers shared by the report endpoint and the legacy endpoints -------------------


async def ingest_alert(alert: Alert) -> tuple[Alert, bool]:
    """Stores `alert` (not yet inserted) and runs grouping + route state. Returns
    `(stored_alert, created)`; `created` is False when an alert with the same
    `(id_route, client_event_id)` already exists (that one is returned, nothing changes).
    An escalation (an earlier medium alert of the same episode) gets `related_alert_id`, and a
    driver-OK already stored for the episode sets `driver_ok_at` (C2.2 rule 6)."""
    async with route_lock(alert.id_route):
        if alert.client_event_id:
            existing = await Alert.find_one(
                Alert.id_route == alert.id_route, Alert.client_event_id == alert.client_event_id
            )
            if existing is not None:
                return existing, False
        if alert.episode:
            if alert.related_alert_id is None and alert.severity_level == Severity.CRITICAL:
                medium = (
                    await Alert.find(
                        Alert.id_route == alert.id_route,
                        Alert.episode == alert.episode,
                        Alert.severity_level == Severity.MEDIUM,
                    )
                    .sort(-Alert.timestamp)
                    .first_or_none()
                )
                if medium is not None:
                    alert.related_alert_id = str(medium.id)
            if alert.driver_ok_at is None:
                ok = (
                    await DriverOk.find(
                        DriverOk.id_route == alert.id_route, DriverOk.episode == alert.episode
                    )
                    .sort(+DriverOk.occurred_at)
                    .first_or_none()
                )
                if ok is not None:
                    alert.driver_ok_at = as_utc(ok.occurred_at)
        try:
            await alert.insert()
        except DuplicateKeyError:
            existing = (
                await Alert.find_one(
                    Alert.id_route == alert.id_route,
                    Alert.client_event_id == alert.client_event_id,
                )
                if alert.client_event_id
                else None
            )
            if existing is None:
                raise
            return existing, False
        if alert.severity_level in GROUPED_SEVERITIES:
            await group_alert(alert)
            await raise_route_state(alert.id_route, alert.severity_level, alert.timestamp)
    return alert, True


async def newest_alert_of_episode(route_id: str, episode: str) -> Optional[Alert]:
    return (
        await Alert.find(Alert.id_route == route_id, Alert.episode == episode)
        .sort(-Alert.timestamp)
        .first_or_none()
    )


async def ingest_driver_ok(driver_ok: DriverOk) -> bool:
    """Stores a driver-OK (not yet inserted) and sets `driver_ok_at` on every alert of its
    `(id_route, episode)` that has none (C2.2 rule 7). Never closes anything and never changes
    `incident_state`. Returns False for a duplicate `(id_route, client_event_id)`."""
    async with route_lock(driver_ok.id_route):
        existing = await DriverOk.find_one(
            DriverOk.id_route == driver_ok.id_route,
            DriverOk.client_event_id == driver_ok.client_event_id,
        )
        if existing is not None:
            return False
        try:
            await driver_ok.insert()
        except DuplicateKeyError:
            return False
        await mark_driver_ok(
            await Alert.find(
                Alert.id_route == driver_ok.id_route, Alert.episode == driver_ok.episode
            ).to_list(),
            as_utc(driver_ok.occurred_at),
        )
    return True


async def mark_driver_ok(alerts: Iterable[Alert], when: datetime) -> None:
    """Sets `driver_ok_at = when` on the alerts that have none and refreshes their incidents.
    Call under the route's lock."""
    touched: set[str] = set()
    for alert in alerts:
        if alert.driver_ok_at is not None:
            continue
        await Alert.get_motor_collection().update_one(
            {"_id": alert.id}, {"$set": {"driver_ok_at": when}}
        )
        alert.driver_ok_at = when
        if alert.id_incident:
            touched.add(alert.id_incident)
    for incident_id in touched:
        await recompute_incident(incident_id)


async def close_incident(incident: Incident, user_id: str, note: str) -> Incident:
    """Closes `incident` and all of its alerts (`closed_at`, `reviewed_by_operator`), then lowers
    the route state (C3.5). 409 if already closed; 422 if critical and `note` is empty."""
    async with route_lock(incident.id_route):
        incident = await get_by_id(Incident, incident.id)
        if incident is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Incident not found")
        if incident.closed_at is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Incident is already closed")
        if incident.severity == Severity.CRITICAL and not note:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "A note is required to close a critical incident",
            )
        now = utcnow()
        await Incident.get_motor_collection().update_one(
            {"_id": incident.id},
            {"$set": {"closed_at": now, "closed_by": user_id, "close_note": note}},
        )
        await Alert.get_motor_collection().update_many(
            {"id_incident": str(incident.id)},
            {"$set": {"closed_at": now, "reviewed_by_operator": True}},
        )
        await lower_route_state_after_close(incident.id_route)
        return await get_by_id(Incident, incident.id)


# --- Output ----------------------------------------------------------------------------------

_TIER_ORDER = {"critical": 0, "medium": 1, "old": 2}


def incident_tier(incident: Incident, now: datetime) -> str:
    if now - as_utc(incident.last_alert_at) > OLD_INCIDENT_AFTER:
        return "old"
    return incident.severity.value


def feed_sort_key(tier: str, last_alert_at: datetime) -> tuple:
    """Tier (critical, medium, old), then newest last alert first."""
    return (_TIER_ORDER[tier], -as_utc(last_alert_at).timestamp())


async def incidents_out(incidents: list[Incident], now: Optional[datetime] = None, *, feed: bool):
    """Builds `IncidentOut`s with their alerts, route-ended flag and truck/driver names, using
    a fixed number of queries whatever the count. `feed=True` sorts in feed order (C3.4),
    otherwise newest `last_alert_at` first."""
    from app.schemas.incident import IncidentOut
    from app.serializers import alert_out

    now = now or utcnow()
    if not incidents:
        return []
    ids = [str(i.id) for i in incidents]
    alerts = await Alert.find(In(Alert.id_incident, ids)).sort(+Alert.timestamp).to_list()
    by_incident: dict[str, list] = {i: [] for i in ids}
    for alert in alerts:
        by_incident.setdefault(alert.id_incident, []).append(alert)

    route_oids = [oid for oid in {to_object_id(i.id_route) for i in incidents} if oid is not None]
    routes = {str(r.id): r for r in await Route.find(In(Route.id, route_oids)).to_list()}
    truck_oids = [o for o in {to_object_id(r.id_truck) for r in routes.values()} if o is not None]
    driver_oids = [o for o in {to_object_id(r.id_driver) for r in routes.values()} if o is not None]
    trucks = {str(t.id): t for t in await Truck.find(In(Truck.id, truck_oids)).to_list()}
    drivers = {str(d.id): d for d in await Driver.find(In(Driver.id, driver_oids)).to_list()}

    out = []
    for incident in incidents:
        route = routes.get(incident.id_route)
        truck = trucks.get(route.id_truck) if route else None
        driver = drivers.get(route.id_driver) if route else None
        out.append(
            IncidentOut(
                id_incident=str(incident.id),
                id_route=incident.id_route,
                severity=incident.severity,
                tier=incident_tier(incident, now),
                first_alert_at=incident.first_alert_at,
                last_alert_at=incident.last_alert_at,
                alert_count=incident.alert_count,
                driver_ok_at=incident.driver_ok_at,
                has_replayed=incident.has_replayed,
                closed_at=incident.closed_at,
                closed_by=incident.closed_by,
                close_note=incident.close_note,
                route_ended=bool(route and route.operative_status in FINISHED_ROUTE_STATUSES),
                truck_plate_number=truck.plate_number if truck else None,
                driver_full_name=f"{driver.first_name} {driver.last_name}" if driver else None,
                alerts=[alert_out(a) for a in by_incident.get(str(incident.id), [])],
            )
        )
    if feed:
        out.sort(key=lambda i: feed_sort_key(i.tier, i.last_alert_at))
    else:
        out.sort(key=lambda i: -as_utc(i.last_alert_at).timestamp())
    return out
