from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.geo import Coordinates
from app.models.common import RouteStatus

from .status_route import StatusRouteOut


class RouteCreate(BaseModel):
    id_driver: str
    id_truck: str
    origin_name: str
    destination_name: str
    destination_coordinates: Coordinates
    estimated_departure: datetime
    estimated_arrival: Optional[datetime] = None
    operative_status: RouteStatus = RouteStatus.SCHEDULED


class RouteUpdate(BaseModel):
    id_driver: Optional[str] = None
    id_truck: Optional[str] = None
    origin_name: Optional[str] = None
    destination_name: Optional[str] = None
    destination_coordinates: Optional[Coordinates] = None
    estimated_departure: Optional[datetime] = None
    estimated_arrival: Optional[datetime] = None
    actual_departure: Optional[datetime] = None
    actual_arrival: Optional[datetime] = None
    operative_status: Optional[RouteStatus] = None


class RouteOut(BaseModel):
    id_route: str
    id_driver: str
    id_truck: str
    origin_name: str
    destination_name: str
    destination_coordinates: Coordinates
    estimated_departure: datetime
    estimated_arrival: Optional[datetime]
    actual_departure: Optional[datetime]
    actual_arrival: Optional[datetime]
    operative_status: RouteStatus
    created_at: datetime
    updated_at: datetime


class RouteWithStatus(RouteOut):
    """`GET /api/routes/active`'s response shape — a route plus its newest Status_Route
    snapshot and light truck/driver refs, so the live dashboard doesn't need a follow-up fetch
    per marker. See the backend CLAUDE.md's "routes/active" section for why this is a dedicated
    endpoint rather than an overloaded `?status=active` filter."""

    latest_status: Optional[StatusRouteOut] = None
    truck_plate_number: Optional[str] = None
    driver_full_name: Optional[str] = None
