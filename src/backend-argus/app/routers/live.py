"""`GET /api/live`: everything the dashboard polls every 5 s, in one request
(docs/designs/final-tweaks-contracts.md, C3.6)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from beanie.operators import In
from fastapi import APIRouter, Depends, Query

from app.auth.dependencies import get_current_user
from app.constants import LIVE_SINCE_OVERLAP, LIVE_TRACK_INITIAL_WINDOW
from app.ids import get_by_id, get_or_404
from app.incidents import as_utc, incidents_out, utcnow
from app.models.driver import Driver
from app.models.incident import Incident
from app.models.route import Route
from app.models.status_route import StatusRoute
from app.models.truck import Truck
from app.report import ACTIVE_ROUTE_STATUSES
from app.schemas.live import LiveResponse, RouteLive
from app.serializers import live_position_out, route_out, track_point_out

router = APIRouter(prefix="/api/live", tags=["live"])


async def route_live_out(route: Route) -> RouteLive:
    latest = (
        await StatusRoute.find(StatusRoute.id_route == str(route.id))
        .sort(-StatusRoute.timestamp)
        .first_or_none()
    )
    truck = await get_by_id(Truck, route.id_truck)
    driver = await get_by_id(Driver, route.id_driver)
    return RouteLive(
        **route_out(route).model_dump(),
        truck_plate_number=truck.plate_number if truck else None,
        driver_full_name=f"{driver.first_name} {driver.last_name}" if driver else None,
        latest_position=live_position_out(latest) if latest else None,
    )


@router.get("", response_model=LiveResponse, dependencies=[Depends(get_current_user)])
async def live(
    since: Optional[datetime] = Query(default=None),
    route: Optional[str] = Query(default=None),
) -> LiveResponse:
    """Without `route`: the active routes, every open incident of any route (an ended route's
    included, `route_ended: true`), and the active routes' track. With `route=<id>`: that one
    route (any status; 404 if unknown), its open incidents and its track. The track is the last
    15 min without `since`, else the points received after `since - LIVE_SINCE_OVERLAP`
    (clamped to 15 min; the overlap catches points committed just after the previous poll's
    query, the UI de-duplicates). Pass `server_time` back as `since` on the next poll. Routes and
    incidents always come in full."""
    server_time = utcnow()  # first, before any query (C3.6)
    if route is not None:
        routes = [await get_or_404(Route, route, "Route")]
        open_query = {"id_route": route, "closed_at": None}
    else:
        routes = await Route.find(
            {"operative_status": {"$in": [s.value for s in ACTIVE_ROUTE_STATUSES]}}
        ).to_list()
        open_query = {"closed_at": None}

    incidents = await Incident.find(open_query).to_list()
    route_ids = [str(r.id) for r in routes]
    floor = server_time - LIVE_TRACK_INITIAL_WINDOW
    if since is None:
        time_filter = StatusRoute.timestamp >= floor
    else:
        since = as_utc(since) - LIVE_SINCE_OVERLAP
        time_filter = StatusRoute.received_at > max(since, floor)
    points = (
        await StatusRoute.find(In(StatusRoute.id_route, route_ids), time_filter)
        .sort(+StatusRoute.id_route, +StatusRoute.timestamp)
        .to_list()
        if route_ids
        else []
    )
    return LiveResponse(
        server_time=server_time,
        routes=[await route_live_out(r) for r in routes],
        incidents=await incidents_out(incidents, server_time, feed=True),
        track=[track_point_out(p) for p in points],
    )
