from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.auth.dependencies import get_current_user, get_current_user_optional, get_device_api_key
from app.geo import to_geojson
from app.ids import get_or_404
from app.ingestion import authorize_route_write
from app.models.route import Route
from app.models.status_route import StatusRoute
from app.models.user import User
from app.report import store_points
from app.schemas.status_route import StatusRouteCreate, StatusRouteOut
from app.serializers import status_route_out

router = APIRouter(prefix="/api/routes", tags=["status_route"])


@router.get("/{route_id}/status", response_model=StatusRouteOut)
async def get_latest_status(
    route_id: str, _user: User = Depends(get_current_user)
) -> StatusRouteOut:
    """The route's newest status by *event* time. Any logged-in role; it's a truck's live
    position, so not anonymous and not a device key (BE-01)."""
    await get_or_404(Route, route_id, "Route")
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
    response: Response,
    user: User | None = Depends(get_current_user_optional),
    device_key: str | None = Depends(get_device_api_key),
) -> StatusRouteOut:
    """Legacy ingestion endpoint (old firmware/simulator; new ones send points in
    `POST /api/devices/me/report`). Also accepts a root_admin/guardian JWT for manual testing.
    A retry with the same `client_event_id`, or a point at a time already stored for this
    route, returns the stored row with 200 instead of a duplicate. No `vigilance` stores
    `unknown`. Stored points update the route's incident state like report points."""
    route = await get_or_404(Route, route_id, "Route")
    await authorize_route_write(route, device_key, user)

    if body.client_event_id:
        existing = await StatusRoute.find_one(
            StatusRoute.id_route == route_id,
            StatusRoute.client_event_id == body.client_event_id,
        )
        if existing is not None:
            response.status_code = status.HTTP_200_OK
            return status_route_out(existing)

    record = StatusRoute(
        id_route=route_id,
        current_coordinates=to_geojson(body.current_coordinates),
        current_speed=body.current_speed,
        odometer=body.odometer,
        vigilance=body.vigilance,
        timestamp=body.event_time(),
        time_source=body.time_source,
        location_source=body.location_source,
        location_accuracy_m=body.location_accuracy_m,
        client_event_id=body.client_event_id,
    )
    if not await store_points(route_id, [record]):
        existing = await StatusRoute.find_one(
            StatusRoute.id_route == route_id, StatusRoute.timestamp == record.timestamp
        )
        if existing is not None:
            response.status_code = status.HTTP_200_OK
            return status_route_out(existing)
    return status_route_out(record)
