from __future__ import annotations

from typing import Optional

from beanie import PydanticObjectId
from beanie.operators import In
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth.dependencies import get_current_user, require_role
from app.geo import to_geojson
from app.models.common import Role, RouteStatus
from app.models.driver import Driver
from app.models.route import Route
from app.models.status_route import StatusRoute
from app.models.truck import Truck
from app.schemas.common import Message
from app.schemas.route import RouteCreate, RouteOut, RouteUpdate, RouteWithStatus
from app.serializers import route_out, route_with_status_out

router = APIRouter(prefix="/api/routes", tags=["routes"])

_ACTIVE_STATUSES = (RouteStatus.IN_PROGRESS, RouteStatus.IN_PROGRESS_ALERT)


async def _get_or_404(route_id: str) -> Route:
    try:
        oid = PydanticObjectId(route_id)
    except Exception:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Route not found")
    route = await Route.get(oid)
    if route is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Route not found")
    return route


@router.get("", response_model=list[RouteOut], dependencies=[Depends(get_current_user)])
async def list_routes(
    status_filter: Optional[RouteStatus] = Query(default=None, alias="status"),
    driver_id: Optional[str] = Query(default=None),
    truck_id: Optional[str] = Query(default=None),
) -> list[RouteOut]:
    query: dict = {}
    if status_filter is not None:
        query["operative_status"] = status_filter
    if driver_id is not None:
        query["id_driver"] = driver_id
    if truck_id is not None:
        query["id_truck"] = truck_id
    routes = await Route.find(query).to_list()
    return [route_out(r) for r in routes]


@router.get(
    "/active", response_model=list[RouteWithStatus], dependencies=[Depends(get_current_user)]
)
async def list_active_routes() -> list[RouteWithStatus]:
    """Backs the live dashboard (`LiveOps.tsx`): in-progress routes, each embedded with its
    newest `Status_Route` snapshot plus light truck/driver refs, so the frontend doesn't need a
    follow-up fetch per marker. A dedicated endpoint rather than an overloaded `?status=active`
    filter, since `RouteStatus` has no such value — see the backend CLAUDE.md for the full
    rationale. One query per route for the latest status; fine at this project's fleet sizes
    (dozens of trucks), a `$lookup` aggregation is the documented upgrade path if that changes."""
    routes = await Route.find(In(Route.operative_status, list(_ACTIVE_STATUSES))).to_list()

    out: list[RouteWithStatus] = []
    for route in routes:
        latest_status = (
            await StatusRoute.find(StatusRoute.id_route == str(route.id))
            .sort(-StatusRoute.timestamp)
            .first_or_none()
        )
        truck = await Truck.get(PydanticObjectId(route.id_truck)) if route.id_truck else None
        driver = await Driver.get(PydanticObjectId(route.id_driver)) if route.id_driver else None
        out.append(route_with_status_out(route, latest_status, truck, driver))
    return out


@router.post(
    "",
    response_model=RouteOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role(Role.ROOT_ADMIN))],
)
async def create_route(body: RouteCreate) -> RouteOut:
    route = Route(
        id_driver=body.id_driver,
        id_truck=body.id_truck,
        origin_name=body.origin_name,
        destination_name=body.destination_name,
        destination_coordinates=to_geojson(body.destination_coordinates),
        estimated_departure=body.estimated_departure,
        estimated_arrival=body.estimated_arrival,
        operative_status=body.operative_status,
    )
    await route.insert()
    return route_out(route)


@router.get("/{route_id}", response_model=RouteOut, dependencies=[Depends(get_current_user)])
async def get_route(route_id: str) -> RouteOut:
    return route_out(await _get_or_404(route_id))


@router.put(
    "/{route_id}",
    response_model=RouteOut,
    dependencies=[Depends(require_role(Role.ROOT_ADMIN))],
)
async def update_route(route_id: str, body: RouteUpdate) -> RouteOut:
    route = await _get_or_404(route_id)
    data = body.model_dump(exclude_unset=True)
    if "destination_coordinates" in data:
        route.destination_coordinates = to_geojson(body.destination_coordinates)
        del data["destination_coordinates"]
    for field, value in data.items():
        setattr(route, field, value)
    await route.touch_and_save()
    return route_out(route)


@router.delete(
    "/{route_id}",
    response_model=Message,
    dependencies=[Depends(require_role(Role.ROOT_ADMIN))],
)
async def delete_route(route_id: str) -> Message:
    route = await _get_or_404(route_id)
    await route.delete()
    return Message(detail="Route deleted")
