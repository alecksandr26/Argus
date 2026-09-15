from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.geo import Coordinates
from app.models.common import Severity


class StatusRouteCreate(BaseModel):
    """The body the ESP32 (or a privileged JWT, for manual testing) posts to
    `POST /api/routes/{id}/status`. Coordinates/speed/odometer are required here, not optional
    — cv-argus never produces geolocation itself (see the backend CLAUDE.md's "Coordination
    note"); the ESP32 is expected to have already attached its own GPS reading by the time this
    request is made."""

    current_coordinates: Coordinates
    current_speed: float
    odometer: float
    vigilance: Severity = Severity.LOW


class StatusRouteOut(BaseModel):
    id_status_route: str
    id_route: str
    current_coordinates: Coordinates
    current_speed: float
    odometer: float
    vigilance: Severity
    timestamp: datetime
