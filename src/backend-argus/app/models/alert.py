from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import pymongo
from beanie import Document
from pydantic import BaseModel, Field

from ..geo import GeoJSONPoint
from .common import AlertSeverity


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


class Alert(Document):
    """An Alert row. Like Status_Route, this is an append-only event record — no
    `TimestampedDocument` mixin — except for `reviewed_by_operator`/`operator_notes`, which a
    guardian/root_admin can edit in place via `PUT /api/alerts/{id}` after creation."""

    id_route: str
    alert_type: str
    severity_level: AlertSeverity
    ai_metadata: AlertAiMetadata
    media_url: Optional[str] = None
    coordinates: GeoJSONPoint
    speed_at_event: float
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # ER diagram spells this `reviwed_by_operator` — fixed here, see models/common.py's docstring.
    reviewed_by_operator: bool = False
    operator_notes: str = ""

    class Settings:
        name = "alerts"
        indexes = [
            pymongo.IndexModel([("id_route", pymongo.ASCENDING)]),
            pymongo.IndexModel([("severity_level", pymongo.ASCENDING)]),
            pymongo.IndexModel([("reviewed_by_operator", pymongo.ASCENDING)]),
            pymongo.IndexModel([("coordinates", pymongo.GEOSPHERE)]),
        ]
