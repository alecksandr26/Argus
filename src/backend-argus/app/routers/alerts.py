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
from app.geo import to_geojson
from app.models.alert import Alert
from app.models.common import AlertSeverity, Role
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
    severity: Optional[AlertSeverity] = Query(default=None),
    reviewed: Optional[bool] = Query(default=None),
    route_id: Optional[str] = Query(default=None),
) -> list[AlertOut]:
    query: dict = {}
    if severity is not None:
        query["severity_level"] = severity
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
        ai_metadata=body.ai_metadata,
        media_url=body.media_url,
        coordinates=to_geojson(body.coordinates),
        speed_at_event=body.speed_at_event,
    )
    await alert.insert()
    return alert_out(alert)


@router.put(
    "/{alert_id}",
    response_model=AlertOut,
    dependencies=[Depends(require_role(Role.ROOT_ADMIN, Role.GUARDIAN))],
)
async def review_alert(alert_id: str, body: AlertReview) -> AlertOut:
    alert = await _get_or_404(alert_id)
    alert.reviewed_by_operator = body.reviewed_by_operator
    alert.operator_notes = body.operator_notes
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
