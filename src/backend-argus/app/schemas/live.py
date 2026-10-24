"""`GET /api/live` and `GET /api/routes/{id}/track` (docs/designs/final-tweaks-contracts.md,
C3.6)."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from app.models.common import Vigilance
from app.schemas.common import UtcDatetime
from app.schemas.incident import IncidentOut
from app.schemas.route import RouteOut


class LivePosition(BaseModel):
    t: UtcDatetime
    lat: float
    lon: float
    speed: float
    vigilance: Vigilance
    last_known: bool
    location_age_s: Optional[float]


class TrackPoint(LivePosition):
    id_route: str


class RouteLive(RouteOut):
    truck_plate_number: Optional[str] = None
    driver_full_name: Optional[str] = None
    latest_position: Optional[LivePosition] = None


class LiveResponse(BaseModel):
    server_time: UtcDatetime
    routes: list[RouteLive]
    incidents: list[IncidentOut]
    track: list[TrackPoint]


class TrackResponse(BaseModel):
    id_route: str
    downsampled: bool
    points: list[TrackPoint]
