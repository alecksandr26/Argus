from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.geo import Coordinates
from app.schemas.common import UtcDatetime
from app.schemas.ingestion import IngestionProvenance, LocationSource, TimeSource
from app.models.alert import AlertAiMetadata, GripStatus, validate_source_ai_metadata
from app.models.common import AlertSource, Severity


class AlertCreate(IngestionProvenance):
    """The body the ESP32 (or a privileged JWT) posts to `POST /api/alerts`. `coordinates`/
    `speed_at_event` are required — see StatusRouteCreate's docstring; the same reasoning
    applies here."""

    id_route: str
    alert_type: str
    severity_level: Severity
    source: AlertSource
    ai_metadata: Optional[AlertAiMetadata] = None
    grip_status: Optional[GripStatus] = None
    related_alert_id: Optional[str] = None
    media_url: Optional[str] = None
    coordinates: Coordinates
    speed_at_event: float = Field(ge=0, allow_inf_nan=False)

    @field_validator("related_alert_id")
    @classmethod
    def _empty_related_id_is_none(cls, value: Optional[str]) -> Optional[str]:
        # The firmware used to send "" for "no link" (FUS-07, X-12).
        return value or None

    @model_validator(mode="after")
    def _check_source_ai_metadata(self) -> "AlertCreate":
        validate_source_ai_metadata(self.source, self.ai_metadata, self.grip_status)
        return self


class AlertReview(BaseModel):
    """`PUT /api/alerts/{id}` — the fields a guardian/root_admin (reviewing an alert) or the
    device that owns its truck (marking it resolved) can change after the fact. A device-key
    caller may only set `resolved_at`; see `app/routers/alerts.py`'s `review_alert`."""

    reviewed_by_operator: Optional[bool] = None
    operator_notes: Optional[str] = None
    resolved_at: Optional[datetime] = None


class AlertResolve(BaseModel):
    """`POST /api/alerts/{id}/resolve` — the device marking a fused incident recovered. A POST
    twin of `PUT`'s `resolved_at`, because the SIM800L's HTTP stack can't send PUT (BE-09)."""

    resolved_at: datetime


class AlertDeescalate(BaseModel):
    """`POST /api/alerts/{id}/de-escalate` — a guardian/root_admin stepping an alert's severity
    down after confirming with the driver. `severity_level` must be strictly lower than the
    alert's current one; `reason` is required so the override is always explained."""

    severity_level: Severity
    reason: str = Field(min_length=1)


class AlertOut(BaseModel):
    id_alert: str
    id_route: str
    alert_type: str
    severity_level: Severity
    source: AlertSource
    ai_metadata: Optional[AlertAiMetadata]
    grip_status: Optional[GripStatus]
    related_alert_id: Optional[str]
    resolved_at: Optional[UtcDatetime]
    original_severity_level: Optional[Severity]
    deescalated_by: Optional[str]
    deescalated_at: Optional[UtcDatetime]
    deescalation_reason: str
    media_url: Optional[str]
    coordinates: Coordinates
    speed_at_event: float
    timestamp: UtcDatetime
    received_at: UtcDatetime
    time_source: TimeSource
    location_source: LocationSource
    location_accuracy_m: Optional[float]
    client_event_id: Optional[str]
    reviewed_by_operator: bool
    operator_notes: str
