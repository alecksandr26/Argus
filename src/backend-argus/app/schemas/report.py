"""`POST /api/devices/me/report`, the one device write (docs/designs/final-tweaks-contracts.md,
C2). Three levels of validation, on purpose:
- the envelope (`ReportEnvelope`): a violation is a 422 for the whole request;
- each point (`ReportPoint`): an invalid point is skipped, the rest are stored;
- the event (`ReportEvent`): an invalid event is answered `rejected invalid`, the points are
  still stored.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from app.constants import REPORT_MAX_POINTS

# 7 days, the same backlog window as the legacy endpoints (`MAX_EVENT_AGE`).
MAX_AGE_S = 604800.0

_Finite = dict(allow_inf_nan=False)


class ReportEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    points: list[Any] = Field(max_length=REPORT_MAX_POINTS)
    event: Optional[dict[str, Any]] = None


class ReportPoint(BaseModel):
    model_config = ConfigDict(extra="ignore")

    age_s: float = Field(ge=0, le=MAX_AGE_S, **_Finite)
    lat: float = Field(ge=-90.0, le=90.0, **_Finite)
    lon: float = Field(ge=-180.0, le=180.0, **_Finite)
    speed: float = Field(ge=0, **_Finite)
    vigilance: Literal["low", "medium", "critical", "unknown"]
    last_known: StrictBool = False
    location_age_s: float = Field(default=0.0, ge=0, **_Finite)


class EventPosition(BaseModel):
    model_config = ConfigDict(extra="ignore")

    lat: float = Field(ge=-90.0, le=90.0, **_Finite)
    lon: float = Field(ge=-180.0, le=180.0, **_Finite)
    last_known: StrictBool = False
    location_age_s: float = Field(default=0.0, ge=0, **_Finite)


class EventScores(BaseModel):
    model_config = ConfigDict(extra="ignore")

    not_drowsy: float = Field(**_Finite)
    drowsy: float = Field(**_Finite)


class EventAiMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: Optional[str] = None
    scores: Optional[EventScores] = None


class ReportEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    client_event_id: str = Field(min_length=1, max_length=64)
    episode: str = Field(min_length=1, max_length=64)
    kind: Literal["alert", "ok"]
    severity: Optional[Literal["medium", "critical"]] = None
    alert_type: Optional[str] = Field(default=None, min_length=1, max_length=64)
    # Not capped here: older than MAX_AGE_S is `rejected too_old`, not `invalid`.
    age_s: float = Field(ge=0, **_Finite)
    delivery: Literal["live", "dlq"]
    queued_seconds: float = Field(default=0.0, ge=0, **_Finite)
    position: Optional[EventPosition] = None
    speed: Optional[float] = Field(default=None, ge=0, **_Finite)
    ai_metadata: Optional[EventAiMetadata] = None

    @model_validator(mode="after")
    def _kind_fields(self) -> "ReportEvent":
        if self.kind == "alert":
            if self.severity is None or self.alert_type is None:
                raise ValueError("an alert needs severity and alert_type")
        elif self.severity is not None or self.alert_type is not None:
            raise ValueError("an ok event has no severity or alert_type")
        return self


PointsStatus = Literal["ok", "no_active_route", "route_finished"]
EventStatus = Literal["created", "duplicate", "rejected"]
RejectReason = Literal["route_finished", "no_route", "too_old", "wrong_truck", "invalid"]


class PointsResult(BaseModel):
    stored: int
    route: Optional[str]
    status: PointsStatus


class EventResult(BaseModel):
    status: EventStatus
    reason: Optional[RejectReason] = None
    id_alert: Optional[str] = None


class ReportResponse(BaseModel):
    points: PointsResult
    event: Optional[EventResult]
    active_route: Optional[str]
