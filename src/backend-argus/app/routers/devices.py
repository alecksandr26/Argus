"""Endpoints a device calls about itself, identified by its API key alone."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.dependencies import get_device_truck
from app.models.common import RouteStatus
from app.models.route import Route
from app.models.truck import Truck
from app.schemas.route import RouteOut
from app.serializers import route_out

router = APIRouter(prefix="/api/devices", tags=["devices"])

_ACTIVE = (RouteStatus.IN_PROGRESS, RouteStatus.IN_PROGRESS_ALERT)


@router.get("/me/active-route", response_model=RouteOut)
async def my_active_route(truck: Truck = Depends(get_device_truck)) -> RouteOut:
    """The route this device's truck is currently on, so the ESP32 doesn't need a route id
    baked into its firmware (BE-05). Fetched at boot and again whenever a write returns 404 or
    409. If several are in progress (a data-entry mistake), the latest departure wins."""
    routes = await Route.find(
        Route.id_truck == str(truck.id),
        {"operative_status": {"$in": [s.value for s in _ACTIVE]}},
    ).to_list()
    if not routes:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No active route for this truck")
    return route_out(max(routes, key=lambda r: r.estimated_departure))
