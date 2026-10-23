from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.auth.dependencies import (
    get_current_user,
    get_current_user_optional,
    get_device_api_key,
    require_role,
)
from app.geo import to_geojson
from app.ids import get_by_id, get_or_404
from app.incidents import (
    FINISHED_ROUTE_STATUSES,
    as_utc,
    ingest_alert,
    mark_driver_ok,
    recompute_incident,
    route_lock,
)
from app.ingestion import INGESTION_ROLES, authorize_route_access, authorize_route_write
from app.models.alert import Alert
from app.models.common import AlertSource, Role, Severity
from app.models.route import Route
from app.models.user import User
from app.schemas.alert import AlertCreate, AlertDeescalate, AlertOut, AlertResolve, AlertReview
from app.schemas.common import Message
from app.serializers import alert_out

router = APIRouter(prefix="/api/alerts", tags=["alerts"])

# How much earlier than the alert's own timestamp a resolved_at may be (device and server
# clocks disagree a little); anything earlier is a bad value, not clock skew (BE-11).
# `resolved_at` is the legacy request field; it now sets the alert's `driver_ok_at`.
RESOLVE_CLOCK_SKEW = timedelta(seconds=60)


async def _get_or_404(alert_id: str) -> Alert:
    return await get_or_404(Alert, alert_id, "Alert")


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


async def _check_related_alert(body: AlertCreate) -> None:
    """An escalation must point at an existing alert on the same route (BE-13)."""
    if body.related_alert_id is None:
        return
    original = await get_by_id(Alert, body.related_alert_id)
    if original is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "related_alert_id not found")
    if original.id_route != body.id_route:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "related_alert_id is on a different route"
        )


async def _resolve(alert: Alert, resolved_at: datetime, *, is_device: bool) -> None:
    """The legacy "driver recovered" (`resolved_at`): sets `driver_ok_at` on `alert` and on
    every earlier alert its escalation chain links back to (BE-12). It never closes anything;
    closing is `POST /api/incidents/{id}/close` (docs/designs/final-tweaks-contracts.md, C2.4).

    Idempotent: an alert that already has `driver_ok_at` keeps it (BE-10). A device gets 409 on
    a finished route (the backend rejects everything from a device for an ended route); a user
    (manual correction) doesn't."""
    if resolved_at.tzinfo is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "resolved_at must include a timezone (UTC)"
        )
    if resolved_at < as_utc(alert.timestamp) - RESOLVE_CLOCK_SKEW:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "resolved_at is earlier than the alert itself"
        )
    route = await get_by_id(Route, alert.id_route)
    if is_device and route is not None and route.operative_status in FINISHED_ROUTE_STATUSES:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Route is {route.operative_status.value}; the device should fetch its active route",
        )
    async with route_lock(alert.id_route):
        chain: list[Alert] = []
        seen: set[str] = set()
        current: Optional[Alert] = await get_by_id(Alert, alert.id)
        while current is not None and str(current.id) not in seen:
            seen.add(str(current.id))
            chain.append(current)
            current = await get_by_id(Alert, current.related_alert_id)
        await mark_driver_ok(chain, as_utc(resolved_at))


@router.get("", response_model=list[AlertOut], dependencies=[Depends(get_current_user)])
async def list_alerts(
    severity: Optional[Severity] = Query(default=None),
    source: Optional[AlertSource] = Query(default=None),
    reviewed: Optional[bool] = Query(default=None),
    route_id: Optional[str] = Query(default=None),
    incident_id: Optional[str] = Query(default=None),
) -> list[AlertOut]:
    query: dict = {}
    if severity is not None:
        query["severity_level"] = severity
    if source is not None:
        query["source"] = source
    if reviewed is not None:
        query["reviewed_by_operator"] = reviewed
    if route_id is not None:
        query["id_route"] = route_id
    if incident_id is not None:
        query["id_incident"] = incident_id
    alerts = await Alert.find(query).sort(-Alert.timestamp).to_list()
    return [alert_out(a) for a in alerts]


@router.get("/{alert_id}", response_model=AlertOut, dependencies=[Depends(get_current_user)])
async def get_alert(alert_id: str) -> AlertOut:
    return alert_out(await _get_or_404(alert_id))


@router.post("", response_model=AlertOut, status_code=status.HTTP_201_CREATED)
async def create_alert(
    body: AlertCreate,
    response: Response,
    user: User | None = Depends(get_current_user_optional),
    device_key: str | None = Depends(get_device_api_key),
) -> AlertOut:
    """Legacy ingestion endpoint (old firmware/simulator; new ones send events in
    `POST /api/devices/me/report`). `source: panic_button` is a 422 (the panic button is
    retired). A medium/critical alert is grouped into an incident and raises the route's
    incident state like a report event; a `low` one gets no incident. A retry with the same
    `client_event_id` returns the stored alert with 200 instead of a duplicate (BE-06)."""
    route = await get_or_404(Route, body.id_route, "Route")
    await authorize_route_write(route, device_key, user)

    if body.client_event_id:
        existing = await Alert.find_one(
            Alert.id_route == body.id_route, Alert.client_event_id == body.client_event_id
        )
        if existing is not None:
            response.status_code = status.HTTP_200_OK
            return alert_out(existing)
    await _check_related_alert(body)

    alert, created = await ingest_alert(
        Alert(
            id_route=body.id_route,
            alert_type=body.alert_type,
            severity_level=body.severity_level,
            source=body.source,
            ai_metadata=body.ai_metadata,
            grip_status=body.grip_status,
            related_alert_id=body.related_alert_id,
            media_url=body.media_url,
            coordinates=to_geojson(body.coordinates),
            speed_at_event=body.speed_at_event,
            timestamp=body.event_time(),
            time_source=body.time_source,
            location_source=body.location_source,
            location_accuracy_m=body.location_accuracy_m,
            client_event_id=body.client_event_id,
        )
    )
    if not created:
        response.status_code = status.HTTP_200_OK
    return alert_out(alert)


@router.put("/{alert_id}", response_model=AlertOut)
async def review_alert(
    alert_id: str,
    body: AlertReview,
    user: User | None = Depends(get_current_user_optional),
    device_key: str | None = Depends(get_device_api_key),
) -> AlertOut:
    """A guardian/root_admin can set `reviewed_by_operator`/`operator_notes` (human review;
    closing is `POST /api/incidents/{id}/close`). The legacy device "resolve" (`resolved_at`,
    device key of the alert's own truck, never the reviewer fields) sets `driver_ok_at`, and is
    409 on a finished route. A user's `resolved_at` sets it too, as a manual correction."""
    alert = await _get_or_404(alert_id)
    is_device = await _is_alerts_own_device(alert, device_key)
    is_privileged_user = user is not None and user.role in INGESTION_ROLES

    if is_device:
        if body.reviewed_by_operator is not None or body.operator_notes is not None:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "A device key may only set resolved_at, not reviewed_by_operator/operator_notes",
            )
    elif user is None:
        # No device key and no token at all — genuinely unauthenticated.
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Requires a valid device API key for this truck, or a root_admin/guardian token",
        )
    elif not is_privileged_user:
        # A real, authenticated user (e.g. `admin`) — just not one of the roles allowed to
        # review an alert. That's a 403 (forbidden), not a 401 (unauthenticated).
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Requires a root_admin/guardian token to review an alert",
        )

    if body.resolved_at is not None:
        await _resolve(alert, body.resolved_at, is_device=is_device)
    review: dict = {}
    if body.reviewed_by_operator is not None:
        review["reviewed_by_operator"] = body.reviewed_by_operator
    if body.operator_notes is not None:
        review["operator_notes"] = body.operator_notes
    if review:
        await Alert.get_motor_collection().update_one({"_id": alert.id}, {"$set": review})
    return alert_out(await _get_or_404(alert_id))


async def _is_alerts_own_device(alert: Alert, device_key: Optional[str]) -> bool:
    route = await get_by_id(Route, alert.id_route)
    if route is None or not device_key:
        return False
    try:
        return await authorize_route_access(route, device_key, None)
    except HTTPException:
        return False


@router.post("/{alert_id}/resolve", response_model=AlertOut)
async def resolve_alert(
    alert_id: str,
    body: AlertResolve,
    user: User | None = Depends(get_current_user_optional),
    device_key: str | None = Depends(get_device_api_key),
) -> AlertOut:
    """The legacy device "driver recovered": a POST twin of `PUT`'s `resolved_at`, since the
    SIM800L's HTTP stack can't send PUT (BE-09). Sets `driver_ok_at`; 409 for a device on a
    finished route. Same callers as that path: the alert's own truck's device key, or a
    root_admin/guardian."""
    alert = await _get_or_404(alert_id)
    route = await get_by_id(Route, alert.id_route)
    if route is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alert's route not found")
    is_device = await authorize_route_access(route, device_key, user)
    await _resolve(alert, body.resolved_at, is_device=is_device)
    return alert_out(await _get_or_404(alert_id))


@router.post("/{alert_id}/de-escalate", response_model=AlertOut)
async def deescalate_alert(
    alert_id: str,
    body: AlertDeescalate,
    user: User = Depends(require_role(Role.ROOT_ADMIN, Role.GUARDIAN)),
) -> AlertOut:
    """Manual severity step-down. The ESP32's fusion loop deliberately never de-escalates on its
    own (see `src/esp32-argus/README.md` section 5) — only a human who has actually checked on
    the driver can. Same roles as reviewing (`review_alert`); never a device key, and never
    `admin`. Also marks the alert reviewed, since confirming with the driver *is* a review."""
    alert = await _get_or_404(alert_id)
    if body.severity_level.rank >= alert.severity_level.rank:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Can only de-escalate to a lower severity than '{alert.severity_level.value}'",
        )

    fields = {
        "original_severity_level": (alert.original_severity_level or alert.severity_level).value,
        "severity_level": body.severity_level.value,
        "deescalated_by": str(user.id),
        "deescalated_at": datetime.now(timezone.utc),
        "deescalation_reason": body.reason,
        "reviewed_by_operator": True,
    }
    async with route_lock(alert.id_route):
        await Alert.get_motor_collection().update_one({"_id": alert.id}, {"$set": fields})
        if alert.id_incident:
            await recompute_incident(alert.id_incident)
    return alert_out(await _get_or_404(alert_id))


@router.delete(
    "/{alert_id}",
    response_model=Message,
    dependencies=[Depends(require_role(Role.ROOT_ADMIN))],
)
async def delete_alert(alert_id: str) -> Message:
    """Its incident is recomputed (and deleted if this was its last alert)."""
    alert = await _get_or_404(alert_id)
    async with route_lock(alert.id_route):
        await alert.delete()
        if alert.id_incident:
            await recompute_incident(alert.id_incident)
    return Message(detail="Alert deleted")
