"""Rules shared by the two device-ingestion endpoints (`POST /api/alerts`,
`POST /api/routes/{id}/status`) and the device resolve path.
"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, status

from app.auth.dependencies import authorize_device_or_user
from app.ids import get_by_id
from app.models.common import Role, RouteStatus
from app.models.route import Route
from app.models.truck import Truck
from app.models.user import User

# Humans allowed to write ingestion records by hand (manual testing, corrections). See the RBAC
# table in CLAUDE.md.
INGESTION_ROLES = (Role.ROOT_ADMIN, Role.GUARDIAN)

_FINISHED = (RouteStatus.COMPLETED, RouteStatus.CANCELLED)


async def authorize_route_access(
    route: Route, device_key: Optional[str], user: Optional[User]
) -> bool:
    """Device key of the route's truck, or a root_admin/guardian token. Returns True for the
    device. 401/403 otherwise (see `authorize_device_or_user`)."""
    truck = await get_by_id(Truck, route.id_truck)
    return await authorize_device_or_user(
        truck=truck, device_key=device_key, user=user, allowed_roles=INGESTION_ROLES
    )


async def authorize_route_write(
    route: Route, device_key: Optional[str], user: Optional[User]
) -> bool:
    """`authorize_route_access` for creating a new record on `route`. A device may only add
    records to a route that hasn't finished (409 otherwise, BE-04): a finished route means the
    device is still configured for an old trip, and its records belong nowhere. Users keep write
    access to finished routes for manual corrections. Resolving an *existing* alert isn't a new
    record and isn't subject to this: an incident can recover after its route ends."""
    is_device = await authorize_route_access(route, device_key, user)
    if is_device and route.operative_status in _FINISHED:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Route is {route.operative_status.value}; the device should fetch its active route",
        )
    return is_device
