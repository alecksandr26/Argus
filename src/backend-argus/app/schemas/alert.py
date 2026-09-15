from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, model_validator

from app.geo import Coordinates
from app.models.alert import AlertAiMetadata, GripStatus, validate_source_ai_metadata
from app.models.common import AlertSource, Severity


class AlertCreate(BaseModel):
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
    speed_at_event: float

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


class AlertOut(BaseModel):
    id_alert: str
    id_route: str
    alert_type: str
    severity_level: Severity
    source: AlertSource
    ai_metadata: Optional[AlertAiMetadata]
    grip_status: Optional[GripStatus]
    related_alert_id: Optional[str]
    resolved_at: Optional[datetime]
    media_url: Optional[str]
    coordinates: Coordinates
    speed_at_event: float
    timestamp: datetime
    reviewed_by_operator: bool
    operator_notes: str
