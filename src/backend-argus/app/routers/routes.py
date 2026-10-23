from __future__ import annotations

from typing import Optional

from beanie.operators import In
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth.dependencies import get_current_user, require_role
from app.constants import TRACK_MAX_POINTS, TRACK_MAX_POINTS_LIMIT
from app.geo import to_geojson
from app.ids import get_by_id, get_or_404
from app.incidents import FINISHED_ROUTE_STATUSES, route_lock, utcnow
from app.models.common import Role, RouteStatus
from app.models.driver import Driver
from app.models.incident import DriverOk, Incident
from app.models.route import Route
from app.models.status_route import StatusRoute
from app.models.truck import Truck
from app.models.user import User
from app.schemas.common import Message
from app.schemas.live import TrackResponse
from app.schemas.route import RouteCreate, RouteOut, RouteStop, RouteUpdate, RouteWithStatus
from app.serializers import route_out, route_with_status_out, track_point_out
from app.track import downsample

router = APIRouter(prefix="/api/routes", tags=["routes"])

_ACTIVE_STATUSES = (RouteStatus.IN_PROGRESS, RouteStatus.IN_PROGRESS_ALERT)


async def _get_or_404(route_id: str) -> Route:
    return await get_or_404(Route, route_id, "Route")


async def _check_refs(id_truck: Optional[str], id_driver: Optional[str]) -> None:
    """A route must point at a truck and a driver that exist (BE-02); a dangling reference
    leaves the device with no truck to authenticate against and the dashboard with no plate."""
    if id_truck is not None and await get_by_id(Truck, id_truck) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "id_truck not found")
    if id_driver is not None and await get_by_id(Driver, id_driver) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "id_driver not found")


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
        truck = await get_by_id(Truck, route.id_truck)
        driver = await get_by_id(Driver, route.id_driver)
        out.append(route_with_status_out(route, latest_status, truck, driver))
    return out


@router.post(
    "",
    response_model=RouteOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role(Role.ROOT_ADMIN, Role.ADMIN))],
)
async def create_route(body: RouteCreate) -> RouteOut:
    """A route created already in progress gets `actual_departure = now` when the body has none
    (and one created completed gets `actual_arrival = now`): device events are matched to
    routes by these times (docs/designs/final-tweaks-contracts.md, C2.2 rule 4)."""
    await _check_refs(body.id_truck, body.id_driver)
    now = utcnow()
    actual_departure = body.actual_departure
    if actual_departure is None and body.operative_status in _ACTIVE_STATUSES:
        actual_departure = now
    route = Route(
        id_driver=body.id_driver,
        id_truck=body.id_truck,
        origin_name=body.origin_name,
        destination_name=body.destination_name,
        destination_coordinates=to_geojson(body.destination_coordinates),
        estimated_departure=body.estimated_departure,
        estimated_arrival=body.estimated_arrival,
        actual_departure=actual_departure,
        actual_arrival=now if body.operative_status == RouteStatus.COMPLETED else None,
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
    dependencies=[Depends(require_role(Role.ROOT_ADMIN, Role.ADMIN))],
)
async def update_route(route_id: str, body: RouteUpdate) -> RouteOut:
    """Edits only the fields sent, as a `$set` (so it can't overwrite the incident state the
    ingest path keeps on the same document). "Start trip" (-> `in_progress`) sets
    `actual_departure = now` if it's still empty; "Complete trip" (-> `completed`) sets
    `actual_arrival = now` likewise (C3.1)."""
    route = await _get_or_404(route_id)
    data = body.model_dump(exclude_unset=True)
    await _check_refs(data.get("id_truck"), data.get("id_driver"))
    if "destination_coordinates" in data:
        data["destination_coordinates"] = to_geojson(body.destination_coordinates).model_dump()
    new_status = data.get("operative_status")
    now = utcnow()
    if new_status in _ACTIVE_STATUSES and route.actual_departure is None:
        data.setdefault("actual_departure", now)
    if new_status == RouteStatus.COMPLETED and route.actual_arrival is None:
        data.setdefault("actual_arrival", now)
    if not data:
        return route_out(route)
    data["updated_at"] = now
    await route.set(data)
    return route_out(await _get_or_404(route_id))


@router.post("/{route_id}/stop", response_model=RouteOut)
async def stop_route(
    route_id: str,
    body: RouteStop,
    user: User = Depends(require_role(Role.ROOT_ADMIN, Role.ADMIN)),
) -> RouteOut:
    """Ends a route early with a required reason (status `cancelled`). From then on the
    backend rejects everything the device sends for it (`points.status: route_finished`,
    events `rejected route_finished`). Open incidents stay open (C3.6)."""
    route = await _get_or_404(route_id)
    async with route_lock(route_id):
        route = await _get_or_404(route_id)
        if route.operative_status in FINISHED_ROUTE_STATUSES:
            raise HTTPException(
                status.HTTP_409_CONFLICT, f"Route is already {route.operative_status.value}"
            )
        now = utcnow()
        fields: dict = {
            "operative_status": RouteStatus.CANCELLED,
            "stop_reason": body.reason,
            "stopped_by": str(user.id),
            "stopped_at": now,
            "updated_at": now,
        }
        if route.actual_departure is not None and route.actual_arrival is None:
            fields["actual_arrival"] = now
        await route.set(fields)
    return route_out(await _get_or_404(route_id))


@router.get(
    "/{route_id}/track", response_model=TrackResponse, dependencies=[Depends(get_current_user)]
)
async def route_track(
    route_id: str,
    max_points: int = Query(default=TRACK_MAX_POINTS, ge=1, le=TRACK_MAX_POINTS_LIMIT),
) -> TrackResponse:
    """A route's whole GPS path, any status, oldest first. Over `max_points`: the first and
    last points, every point whose vigilance isn't `low`, then an even stride of the rest."""
    await _get_or_404(route_id)
    points = await StatusRoute.find(StatusRoute.id_route == route_id).sort(+StatusRoute.timestamp).to_list()
    kept = downsample(points, max_points)
    return TrackResponse(
        id_route=route_id,
        downsampled=len(kept) < len(points),
        points=[track_point_out(p) for p in kept],
    )


@router.delete(
    "/{route_id}",
    response_model=Message,
    dependencies=[Depends(require_role(Role.ROOT_ADMIN, Role.ADMIN))],
)
async def delete_route(route_id: str) -> Message:
    """Also deletes the route's incidents and driver-OKs, so no orphan incident stays in the
    live feed. Its alerts and points are not cascaded (C3.1)."""
    route = await _get_or_404(route_id)
    await route.delete()
    await Incident.find(Incident.id_route == route_id).delete()
    await DriverOk.find(DriverOk.id_route == route_id).delete()
    return Message(detail="Route deleted")
