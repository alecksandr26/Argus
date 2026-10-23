from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, field_validator

from app.constants import CLOSE_NOTE_MAX_LEN
from app.models.common import Severity
from app.schemas.alert import AlertOut
from app.schemas.common import UtcDatetime

IncidentTier = Literal["critical", "medium", "old"]


class IncidentOut(BaseModel):
    """docs/designs/final-tweaks-contracts.md, C3.4."""

    id_incident: str
    id_route: str
    severity: Severity
    tier: IncidentTier
    first_alert_at: UtcDatetime
    last_alert_at: UtcDatetime
    alert_count: int
    driver_ok_at: Optional[UtcDatetime]
    has_replayed: bool
    closed_at: Optional[UtcDatetime]
    closed_by: Optional[str]
    close_note: str
    route_ended: bool
    truck_plate_number: Optional[str]
    driver_full_name: Optional[str]
    alerts: list[AlertOut]


class IncidentClose(BaseModel):
    """`POST /api/incidents/{id}/close`. The note is stripped; it may be empty only for a
    medium incident (checked in the router, which knows the severity)."""

    note: str = ""

    @field_validator("note")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if len(value) > CLOSE_NOTE_MAX_LEN:
            raise ValueError(f"The note is longer than {CLOSE_NOTE_MAX_LEN} characters")
        return value
