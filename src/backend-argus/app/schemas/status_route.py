from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.geo import Coordinates
from app.schemas.common import UtcDatetime
from app.schemas.ingestion import IngestionProvenance, LocationSource, StoredTimeSource
from app.models.common import Vigilance


class StatusRouteCreate(IngestionProvenance):
    """The body the ESP32 (or a privileged JWT, for manual testing) posts to
    `POST /api/routes/{id}/status`. Coordinates/speed/odometer are required here, not optional
    — cv-argus never produces geolocation itself (see the backend CLAUDE.md's "Coordination
    note"); the ESP32 is expected to have already attached its own GPS reading by the time this
    request is made. Legacy: new firmware sends points in `POST /api/devices/me/report`.
    A body without `vigilance` stores `unknown`, never `low`."""

    current_coordinates: Coordinates
    current_speed: float = Field(ge=0, allow_inf_nan=False)
    odometer: float = Field(ge=0, allow_inf_nan=False)
    vigilance: Vigilance = Vigilance.UNKNOWN


class StatusRouteOut(BaseModel):
    id_status_route: str
    id_route: str
    current_coordinates: Coordinates
    current_speed: float
    odometer: float
    vigilance: Vigilance
    location_is_last_known: bool
    location_age_s: Optional[float]
    timestamp: UtcDatetime
    received_at: UtcDatetime
    time_source: StoredTimeSource
    location_source: LocationSource
    location_accuracy_m: Optional[float]
