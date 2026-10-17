from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

import pymongo
from beanie import Document
from pydantic import BaseModel, Field, model_validator

from ..geo import GeoJSONPoint
from .common import AlertSource, Severity


class AlertAiMetadata(BaseModel):
    """Corrected from `ui-argus`'s stale 3-class guess to the project's binary scheme.

    `model`/`clip_seconds` are nullable, not required: confirmed directly with the session
    building `cv-argus`'s `alerts/` module that its current envelope carries neither a
    model-version string nor a clip duration (`FusedDrowsinessDetector` runs a rolling
    100-frame/20s window, not a discrete clip). See the backend CLAUDE.md's "Coordination note"
    for the full exchange. Revisit as required once/if cv-argus starts populating them.
    """

    model: Optional[str] = None
    scores: dict[str, float]  # {"not_drowsy": ..., "drowsy": ...}
    clip_seconds: Optional[float] = None


GripStatus = Literal["good", "bad"]


def validate_source_ai_metadata(
    source: AlertSource,
    ai_metadata: Optional[AlertAiMetadata],
    grip_status: Optional[GripStatus],
) -> None:
    """The one rule shared by `Alert` (this module) and `AlertCreate` (`app/schemas/alert.py`)
    so it can't drift between the two: a `fusion` alert always carries both the CV scores and
    the grip reading that went into its severity decision (see `src/esp32-argus/README.md`'s
    fusion section); a `panic_button` alert carries neither, since no camera/grip evaluation
    happened — it's an unconditional, driver-triggered `critical`.
    """
    if source == AlertSource.FUSION:
        if ai_metadata is None or grip_status is None:
            raise ValueError(
                "ai_metadata and grip_status are both required when source is 'fusion'"
            )
    else:
        if ai_metadata is not None or grip_status is not None:
            raise ValueError(
                "ai_metadata and grip_status must be omitted when source is 'panic_button'"
            )


class Alert(Document):
    """An Alert row. Like Status_Route, this is an append-only event record — no
    `TimestampedDocument` mixin — except for `reviewed_by_operator`/`operator_notes`/
    `resolved_at`, which can be edited in place after creation (see `app/routers/alerts.py`'s
    `review_alert`), and `severity_level` plus the `deescalat*` audit fields, which only a
    manual de-escalation changes (`deescalate_alert`)."""

    id_route: str
    alert_type: str
    severity_level: Severity
    source: AlertSource
    ai_metadata: Optional[AlertAiMetadata] = None
    grip_status: Optional[GripStatus] = None
    # Set when this alert is an escalation of a still-open incident (e.g. a `medium` fusion
    # alert that didn't improve within the escalation window, per fusion_contract.py) — points
    # back at the original Alert's id rather than mutating its severity_level in place, keeping
    # this Document genuinely append-only. No FK enforcement at this project's scale, same as
    # other string-id references elsewhere (id_route, id_truck, etc.).
    related_alert_id: Optional[str] = None
    # Set once the underlying condition recovers (both signals good, held for the recovery
    # window) — the one other legitimately-mutable field besides the reviewer ones above.
    resolved_at: Optional[datetime] = None
    # Manual de-escalation by a guardian/root_admin who has confirmed with the driver that the
    # incident is a false positive or has eased (see `app/routers/alerts.py`'s `deescalate_alert`).
    # The ESP32's fusion loop never steps a severity down on its own, so this is the one path
    # that rewrites `severity_level` in place. `original_severity_level` keeps what the device
    # actually reported (set on the first de-escalation only) so the audit trail survives.
    original_severity_level: Optional[Severity] = None
    deescalated_by: Optional[str] = None  # User id
    deescalated_at: Optional[datetime] = None
    deescalation_reason: str = ""
    media_url: Optional[str] = None
    coordinates: GeoJSONPoint
    speed_at_event: float
    # When the event happened (the device's occurred_at, else server receive time) -- what
    # every "when" in the UI means. received_at is when this backend got it; the gap is how
    # long the record sat in a backlog. time_source says which clock `timestamp` came from.
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    time_source: str = "server"
    location_source: str = "gps"
    location_accuracy_m: Optional[float] = None
    # Device-side id of the event; a retried POST with the same id returns this row (BE-06).
    client_event_id: Optional[str] = None
    # ER diagram spells this `reviwed_by_operator` — fixed here, see models/common.py's docstring.
    reviewed_by_operator: bool = False
    operator_notes: str = ""

    @model_validator(mode="after")
    def _check_source_ai_metadata(self) -> "Alert":
        validate_source_ai_metadata(self.source, self.ai_metadata, self.grip_status)
        return self

    class Settings:
        name = "alerts"
        indexes = [
            pymongo.IndexModel([("id_route", pymongo.ASCENDING)]),
            pymongo.IndexModel(
                [("id_route", pymongo.ASCENDING), ("client_event_id", pymongo.ASCENDING)]
            ),
            pymongo.IndexModel([("severity_level", pymongo.ASCENDING)]),
            pymongo.IndexModel([("source", pymongo.ASCENDING)]),
            pymongo.IndexModel([("reviewed_by_operator", pymongo.ASCENDING)]),
            pymongo.IndexModel([("coordinates", pymongo.GEOSPHERE)]),
        ]
