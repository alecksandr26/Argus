from __future__ import annotations

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.dependencies import (
    authorize_device_or_user,
    get_current_user_optional,
    get_device_api_key,
)
from app.geo import to_geojson
from app.models.common import Role
from app.models.route import Route
from app.models.status_route import StatusRoute
from app.models.truck import Truck
from app.models.user import User
from app.schemas.status_route import StatusRouteCreate, StatusRouteOut
from app.serializers import status_route_out

router = APIRouter(prefix="/api/routes", tags=["status_route"])


async def _get_route_or_404(route_id: str) -> Route:
    try:
        oid = PydanticObjectId(route_id)
    except Exception:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Route not found")
    route = await Route.get(oid)
    if route is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Route not found")
    return route


@router.get("/{route_id}/status", response_model=StatusRouteOut)
async def get_latest_status(
    route_id: str, _user: User = Depends(get_current_user_optional)
) -> StatusRouteOut:
    await _get_route_or_404(route_id)  # 404s a bad route id before we bother querying status
    latest = (
        await StatusRoute.find(StatusRoute.id_route == route_id)
        .sort(-StatusRoute.timestamp)
        .first_or_none()
    )
    if latest is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No status recorded for this route yet")
    return status_route_out(latest)


@router.post(
    "/{route_id}/status", response_model=StatusRouteOut, status_code=status.HTTP_201_CREATED
)
async def post_status(
    route_id: str,
    body: StatusRouteCreate,
    user: User | None = Depends(get_current_user_optional),
    device_key: str | None = Depends(get_device_api_key),
) -> StatusRouteOut:
    """Ingestion endpoint the ESP32 calls after polling a `route_status` record off the Pi's
    Bluetooth buffer and attaching its own GPS reading. Also accepts a root_admin/guardian JWT
    for manual testing/demo seeding without real hardware — see `authorize_device_or_user`."""
    route = await _get_route_or_404(route_id)
    truck = await Truck.get(PydanticObjectId(route.id_truck)) if route.id_truck else None
    await authorize_device_or_user(
        truck=truck,
        device_key=device_key,
        user=user,
        allowed_roles=(Role.ROOT_ADMIN, Role.GUARDIAN),
    )

    record = StatusRoute(
        id_route=route_id,
        current_coordinates=to_geojson(body.current_coordinates),
        current_speed=body.current_speed,
        odometer=body.odometer,
        vigilance=body.vigilance,
    )
    await record.insert()
    return status_route_out(record)
