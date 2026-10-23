from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.geo import Coordinates
from app.schemas.common import UtcDatetime
from app.schemas.ingestion import IngestionProvenance, LocationSource, StoredTimeSource
from app.models.alert import AlertAiMetadata, GripStatus, validate_source_ai_metadata
from app.models.common import AlertSource, Delivery, Severity


class AlertCreate(IngestionProvenance):
    """The legacy body the old ESP32 firmware (or a privileged JWT) posts to `POST /api/alerts`.
    New firmware posts `POST /api/devices/me/report` instead; this stays until every module has
    moved (docs/designs/final-tweaks-contracts.md, C2.4). `coordinates`/`speed_at_event` stay
    required here. `source: panic_button` is a 422: the panic button is retired."""

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

    @field_validator("source")
    @classmethod
    def _no_new_panic_alerts(cls, value: AlertSource) -> AlertSource:
        if value == AlertSource.PANIC_BUTTON:
            raise ValueError("the panic button is retired; panic_button alerts are read-only")
        return value

    @model_validator(mode="after")
    def _check_source_ai_metadata(self) -> "AlertCreate":
        validate_source_ai_metadata(self.source, self.ai_metadata, self.grip_status)
        return self


class AlertReview(BaseModel):
    """`PUT /api/alerts/{id}` — a guardian/root_admin reviewing an alert (notes only; closing
    is `POST /api/incidents/{id}/close`), or the legacy device "resolve" (`resolved_at`, which
    now sets the alert's `driver_ok_at`). A device key may only send `resolved_at` and a user
    may not send it; see `app/routers/alerts.py`'s `review_alert`."""

    reviewed_by_operator: Optional[bool] = None
    operator_notes: Optional[str] = None
    resolved_at: Optional[datetime] = None


class AlertResolve(BaseModel):
    """`POST /api/alerts/{id}/resolve` — the legacy device "driver recovered" (sets
    `driver_ok_at`). A POST twin of `PUT`'s `resolved_at`, because the SIM800L's HTTP stack
    can't send PUT (BE-09). The report endpoint's `kind: ok` event replaces both."""

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
    episode: Optional[str]
    id_incident: Optional[str]
    delivery: Delivery
    queued_seconds: float
    driver_ok_at: Optional[UtcDatetime]
    closed_at: Optional[UtcDatetime]
    ai_metadata: Optional[AlertAiMetadata]
    grip_status: Optional[GripStatus]
    related_alert_id: Optional[str]
    original_severity_level: Optional[Severity]
    deescalated_by: Optional[str]
    deescalated_at: Optional[UtcDatetime]
    deescalation_reason: str
    media_url: Optional[str]
    coordinates: Optional[Coordinates]
    position_unknown: bool
    location_is_last_known: bool
    location_age_s: Optional[float]
    speed_at_event: Optional[float]
    timestamp: UtcDatetime
    received_at: UtcDatetime
    time_source: StoredTimeSource
    location_source: LocationSource
    location_accuracy_m: Optional[float]
    client_event_id: Optional[str]
    reviewed_by_operator: bool
    operator_notes: str
