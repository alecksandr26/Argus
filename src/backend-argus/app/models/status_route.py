from __future__ import annotations

from datetime import datetime, timezone

import pymongo
from beanie import Document
from pydantic import Field

from ..geo import GeoJSONPoint
from .common import Severity


class StatusRoute(Document):
    """A single live-tracking ping for a Route. No `updated_at`/`created_at` mixin — each row is
    an immutable point-in-time snapshot (append-only), not a record that gets edited in place,
    so `TimestampedDocument`'s update-tracking semantics don't apply here."""

    id_route: str
    current_coordinates: GeoJSONPoint
    current_speed: float
    odometer: float
    vigilance: Severity = Severity.LOW
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "status_routes"
        indexes = [
            pymongo.IndexModel(
                [("id_route", pymongo.ASCENDING), ("timestamp", pymongo.DESCENDING)]
            ),
            pymongo.IndexModel([("current_coordinates", pymongo.GEOSPHERE)]),
        ]
