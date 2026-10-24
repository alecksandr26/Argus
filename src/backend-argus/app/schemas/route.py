from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, field_validator

from app.geo import Coordinates
from app.schemas.common import UtcDatetime
from app.constants import STOP_REASON_MAX_LEN
from app.models.common import IncidentState, RouteStatus

from .status_route import StatusRouteOut


class RouteCreate(BaseModel):
    id_driver: str
    id_truck: str
    origin_name: str
    destination_name: str
    destination_coordinates: Coordinates
    estimated_departure: datetime
    estimated_arrival: Optional[datetime] = None
    # Created already in progress (e.g. by the simulator) without this: set to now.
    actual_departure: Optional[datetime] = None
    operative_status: RouteStatus = RouteStatus.SCHEDULED


class RouteStop(BaseModel):
    """`POST /api/routes/{id}/stop` — ending a route early; the reason is required."""

    reason: str

    @field_validator("reason")
    @classmethod
    def _strip_and_require(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("A reason is required to stop a route")
        if len(value) > STOP_REASON_MAX_LEN:
            raise ValueError(f"The reason is longer than {STOP_REASON_MAX_LEN} characters")
        return value


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
    estimated_departure: UtcDatetime
    estimated_arrival: Optional[UtcDatetime]
    actual_departure: Optional[UtcDatetime]
    actual_arrival: Optional[UtcDatetime]
    operative_status: RouteStatus
    incident_state: IncidentState
    last_not_ok_at: Optional[UtcDatetime]
    stop_reason: Optional[str]
    stopped_by: Optional[str]
    stopped_at: Optional[UtcDatetime]
    created_at: UtcDatetime
    updated_at: UtcDatetime


class RouteWithStatus(RouteOut):
    """`GET /api/routes/active`'s response shape — a route plus its newest Status_Route
    snapshot and light truck/driver refs, so the live dashboard doesn't need a follow-up fetch
    per marker. See the backend CLAUDE.md's "routes/active" section for why this is a dedicated
    endpoint rather than an overloaded `?status=active` filter."""

    latest_status: Optional[StatusRouteOut] = None
    truck_plate_number: Optional[str] = None
    driver_full_name: Optional[str] = None
