from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import pymongo
from beanie import Document
from pydantic import Field

from ..geo import GeoJSONPoint
from .common import Vigilance


class StatusRoute(Document):
    """A single live-tracking ping for a Route. No `updated_at`/`created_at` mixin — each row is
    an immutable point-in-time snapshot (append-only), not a record that gets edited in place,
    so `TimestampedDocument`'s update-tracking semantics don't apply here."""

    id_route: str
    current_coordinates: GeoJSONPoint
    current_speed: float
    odometer: float
    # `unknown` when the device had no recent Pi reading or didn't say; never assumed `low`.
    vigilance: Vigilance = Vigilance.UNKNOWN
    # True when the position is the ESP32's saved last good fix (no current fix), and that fix's
    # age in seconds at the time of the point.
    location_is_last_known: bool = False
    location_age_s: Optional[float] = None
    # Retention: True for the first stored point of each (route, UTC minute). Points with
    # keep=False expire after GPS_FULL_RETENTION (partial TTL index, app/indexes.py).
    keep: bool = False
    # Event time (device occurred_at, else receive time); "latest status" sorts on this, so a
    # backlog record arriving late can't replace a newer one. See models/alert.py.
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    time_source: str = "server"
    location_source: str = "gps"
    location_accuracy_m: Optional[float] = None
    client_event_id: Optional[str] = None

    class Settings:
        name = "status_routes"
        indexes = [
            pymongo.IndexModel(
                [("id_route", pymongo.ASCENDING), ("timestamp", pymongo.DESCENDING)]
            ),
            pymongo.IndexModel(
                [("id_route", pymongo.ASCENDING), ("client_event_id", pymongo.ASCENDING)]
            ),
            # `GET /api/live?since=` reads points by receive time.
            pymongo.IndexModel([("received_at", pymongo.ASCENDING)]),
            pymongo.IndexModel([("current_coordinates", pymongo.GEOSPHERE)]),
            # The unique (id_route, timestamp) and TTL indexes live in app/indexes.py.
        ]
