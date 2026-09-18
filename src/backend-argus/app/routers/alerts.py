from __future__ import annotations

from typing import Optional

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth.dependencies import (
    authorize_device_or_user,
    get_current_user,
    get_current_user_optional,
    get_device_api_key,
    require_role,
)
from app.auth.security import verify_secret
from app.geo import to_geojson
from app.models.alert import Alert
from app.models.common import AlertSource, Role, Severity
from app.models.route import Route
from app.models.truck import Truck
from app.models.user import User
from app.schemas.alert import AlertCreate, AlertOut, AlertReview
from app.schemas.common import Message
from app.serializers import alert_out

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


async def _get_or_404(alert_id: str) -> Alert:
    try:
        oid = PydanticObjectId(alert_id)
    except Exception:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alert not found")
    alert = await Alert.get(oid)
    if alert is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alert not found")
    return alert


@router.get("", response_model=list[AlertOut], dependencies=[Depends(get_current_user)])
async def list_alerts(
    severity: Optional[Severity] = Query(default=None),
    source: Optional[AlertSource] = Query(default=None),
    reviewed: Optional[bool] = Query(default=None),
    route_id: Optional[str] = Query(default=None),
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
    alerts = await Alert.find(query).sort(-Alert.timestamp).to_list()
    return [alert_out(a) for a in alerts]


@router.get("/{alert_id}", response_model=AlertOut, dependencies=[Depends(get_current_user)])
async def get_alert(alert_id: str) -> AlertOut:
    return alert_out(await _get_or_404(alert_id))


@router.post("", response_model=AlertOut, status_code=status.HTTP_201_CREATED)
async def create_alert(
    body: AlertCreate,
    user: User | None = Depends(get_current_user_optional),
    device_key: str | None = Depends(get_device_api_key),
) -> AlertOut:
    """Ingestion endpoint the ESP32 calls after polling a `drowsiness` record off the Pi's
    Bluetooth buffer and attaching its own GPS reading. See `status_routes.py`'s `post_status`
    for the identical device-or-user authorization shape, and the backend CLAUDE.md's
    "Coordination note" for exactly what cv-argus's envelope does/doesn't carry."""
    route = await Route.get(PydanticObjectId(body.id_route)) if body.id_route else None
    if route is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Route not found")
    truck = await Truck.get(PydanticObjectId(route.id_truck)) if route.id_truck else None
    await authorize_device_or_user(
        truck=truck,
        device_key=device_key,
        user=user,
        allowed_roles=(Role.ROOT_ADMIN, Role.GUARDIAN),
    )

    alert = Alert(
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
    )
    await alert.insert()
    return alert_out(alert)


@router.put("/{alert_id}", response_model=AlertOut)
async def review_alert(
    alert_id: str,
    body: AlertReview,
    user: User | None = Depends(get_current_user_optional),
    device_key: str | None = Depends(get_device_api_key),
) -> AlertOut:
    """A guardian/root_admin can set `reviewed_by_operator`/`operator_notes` (human review) and/
    or `resolved_at`. A device key (the ESP32 marking a fused incident's recovery — see
    `fusion_contract.py`'s recovery window) may set **only** `resolved_at`, never the reviewer
    fields — see the backend CLAUDE.md's "Coordination note" for why the ESP32, not a human, is
    what detects recovery."""
    alert = await _get_or_404(alert_id)
    route = await Route.get(PydanticObjectId(alert.id_route)) if alert.id_route else None
    truck = (
        await Truck.get(PydanticObjectId(route.id_truck))
        if route is not None and route.id_truck
        else None
    )

    is_device = bool(
        device_key
        and truck is not None
        and truck.device_api_key_hash
        and verify_secret(device_key, truck.device_api_key_hash)
    )
    is_privileged_user = user is not None and user.role in (Role.ROOT_ADMIN, Role.GUARDIAN)

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

    if body.reviewed_by_operator is not None:
        alert.reviewed_by_operator = body.reviewed_by_operator
    if body.operator_notes is not None:
        alert.operator_notes = body.operator_notes
    if body.resolved_at is not None:
        alert.resolved_at = body.resolved_at
    await alert.save()
    return alert_out(alert)


@router.delete(
    "/{alert_id}",
    response_model=Message,
    dependencies=[Depends(require_role(Role.ROOT_ADMIN))],
)
async def delete_alert(alert_id: str) -> Message:
    alert = await _get_or_404(alert_id)
    await alert.delete()
    return Message(detail="Alert deleted")
