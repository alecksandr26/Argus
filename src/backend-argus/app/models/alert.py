from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

import pymongo
from beanie import Document
from pydantic import BaseModel, Field, model_validator

from ..geo import GeoJSONPoint
from .common import AlertSource, Delivery, Severity


class AlertAiMetadata(BaseModel):
    """The Pi rule engine's model name and its last class scores, when it has them.

    Everything is optional: the parts-rules pipeline has no class probabilities on a `No Face`
    reading, and a report event may carry `ai_metadata: null` altogether
    (docs/designs/final-tweaks-contracts.md, C2.1). `clip_seconds` is a leftover from the
    CNN+LSTM envelope; old rows may have it."""

    model: Optional[str] = None
    scores: Optional[dict[str, float]] = None  # {"not_drowsy": ..., "drowsy": ...}
    clip_seconds: Optional[float] = None


GripStatus = Literal["good", "bad"]

# "relative" = computed by the backend from a device `age_s` (every report event, C2.1).
TimeSource = Literal["gps", "network", "server", "relative"]


def validate_source_ai_metadata(
    source: AlertSource,
    ai_metadata: Optional[AlertAiMetadata],
    grip_status: Optional[GripStatus],
) -> None:
    """Shared by `Alert` and `AlertCreate` so the rule can't drift between the two. A
    `panic_button` alert (old rows only; creating one is a 422) carries neither CV scores nor a
    grip reading. A `fusion` alert may carry either, both or none: the ESP32 no longer sends a
    grip reading, and the Pi has no scores on a `No Face` reading (C3.2)."""
    if source == AlertSource.PANIC_BUTTON:
        if ai_metadata is not None or grip_status is not None:
            raise ValueError(
                "ai_metadata and grip_status must be omitted when source is 'panic_button'"
            )


class Alert(Document):
    """An Alert row: one medium/critical event of a Pi drowsiness episode (plus old `low` and
    panic rows, read-only). Append-only except for the review fields (`reviewed_by_operator`,
    `operator_notes`), the de-escalation fields, `driver_ok_at` (the driver recovered), and the
    incident fields (`id_incident`, `closed_at`), which `app.incidents` maintains."""

    id_route: str
    alert_type: str
    severity_level: Severity
    source: AlertSource
    ai_metadata: Optional[AlertAiMetadata] = None
    grip_status: Optional[GripStatus] = None
    # The Pi episode (12 hex chars) this alert belongs to; a medium alert and its critical
    # escalation share it, and so does the driver-OK that ends it. None on old rows.
    episode: Optional[str] = None
    # An escalation points back at the medium alert of its episode instead of rewriting it.
    related_alert_id: Optional[str] = None
    # When the driver recovered (the device's "driver OK"). It never closes anything: only a
    # guardian/root_admin closes the incident (`closed_at`). Replaces the retired `resolved_at`.
    driver_ok_at: Optional[datetime] = None
    # The incident this alert is grouped in (`app.incidents`); None only for `low` alerts.
    id_incident: Optional[str] = None
    closed_at: Optional[datetime] = None
    # `live`, or `dlq` = replayed from the Pi's dead-letter queue, `queued_seconds` late.
    delivery: Delivery = Delivery.LIVE
    queued_seconds: float = 0.0
    # Manual de-escalation by a guardian/root_admin (`app/routers/alerts.py`'s
    # `deescalate_alert`), the one path that rewrites `severity_level` in place.
    # `original_severity_level` keeps what the device reported.
    original_severity_level: Optional[Severity] = None
    deescalated_by: Optional[str] = None  # User id
    deescalated_at: Optional[datetime] = None
    deescalation_reason: str = ""
    media_url: Optional[str] = None
    # The position when the event was first composed; None = position unknown.
    coordinates: Optional[GeoJSONPoint] = None
    # True when `coordinates` is the ESP32's last good fix, not a live one, and its age (s).
    location_is_last_known: bool = False
    location_age_s: Optional[float] = None
    speed_at_event: Optional[float] = None
    # When the event happened -- what every "when" in the UI means. received_at is when this
    # backend got it. time_source says which clock `timestamp` came from.
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    time_source: TimeSource = "server"
    location_source: str = "gps"
    location_accuracy_m: Optional[float] = None
    # Device-side id of the event (the Pi record id). Unique per route (app/indexes.py), so a
    # retried POST finds the row already stored.
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
        # The unique (id_route, client_event_id) index lives in app/indexes.py.
        indexes = [
            pymongo.IndexModel([("id_route", pymongo.ASCENDING)]),
            pymongo.IndexModel([("client_event_id", pymongo.ASCENDING)]),
            pymongo.IndexModel([("id_route", pymongo.ASCENDING), ("episode", pymongo.ASCENDING)]),
            pymongo.IndexModel([("id_incident", pymongo.ASCENDING)]),
            pymongo.IndexModel([("severity_level", pymongo.ASCENDING)]),
            pymongo.IndexModel([("source", pymongo.ASCENDING)]),
            pymongo.IndexModel([("reviewed_by_operator", pymongo.ASCENDING)]),
            pymongo.IndexModel([("coordinates", pymongo.GEOSPHERE)]),
        ]
