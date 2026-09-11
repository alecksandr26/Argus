from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.geo import Coordinates
from app.models.alert import AlertAiMetadata
from app.models.common import AlertSeverity


class AlertCreate(BaseModel):
    """The body the ESP32 (or a privileged JWT) posts to `POST /api/alerts`. `coordinates`/
    `speed_at_event` are required — see StatusRouteCreate's docstring; the same reasoning
    applies here."""

    id_route: str
    alert_type: str
    severity_level: AlertSeverity
    ai_metadata: AlertAiMetadata
    media_url: Optional[str] = None
    coordinates: Coordinates
    speed_at_event: float


class AlertReview(BaseModel):
    """`PUT /api/alerts/{id}` — the only fields a guardian/root_admin can change after the
    fact."""

    reviewed_by_operator: bool
    operator_notes: str = ""


class AlertOut(BaseModel):
    id_alert: str
    id_route: str
    alert_type: str
    severity_level: AlertSeverity
    ai_metadata: AlertAiMetadata
    media_url: Optional[str]
    coordinates: Coordinates
    speed_at_event: float
    timestamp: datetime
    reviewed_by_operator: bool
    operator_notes: str
