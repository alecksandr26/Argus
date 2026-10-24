from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import pymongo
from beanie import Document
from pydantic import Field

from .common import Severity


class Incident(Document):
    """A group of medium/critical alerts of one route that happened close together
    (docs/designs/final-tweaks-contracts.md, C3.4). Every aggregate field is recomputed from its
    alerts by `app.incidents.recompute_incident`, under the route's lock; only closing sets
    `closed_at`/`closed_by`/`close_note`, and a closed incident never gets new alerts."""

    id_route: str
    # Max severity_level of its alerts (never below medium).
    severity: Severity = Severity.MEDIUM
    first_alert_at: datetime
    last_alert_at: datetime
    alert_count: int = 0
    # driver_ok_at of the episode of its newest alert.
    driver_ok_at: Optional[datetime] = None
    has_replayed: bool = False
    closed_at: Optional[datetime] = None
    closed_by: Optional[str] = None  # User id
    close_note: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "incidents"
        indexes = [
            pymongo.IndexModel([("id_route", pymongo.ASCENDING), ("closed_at", pymongo.ASCENDING)]),
            pymongo.IndexModel([("closed_at", pymongo.ASCENDING)]),
            pymongo.IndexModel([("last_alert_at", pymongo.DESCENDING)]),
        ]


class DriverOk(Document):
    """A device "driver OK" event: the Pi episode `episode` ended (the driver recovered). Stored
    on its own so it still applies when its alert arrives later (C2.2 rules 6 and 7). It never
    closes anything. Unique per (id_route, client_event_id), see app/indexes.py."""

    id_route: str
    episode: str
    client_event_id: str
    occurred_at: datetime
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    delivery: str = "live"
    queued_seconds: float = 0.0

    class Settings:
        name = "driver_oks"
        indexes = [
            pymongo.IndexModel([("id_route", pymongo.ASCENDING), ("episode", pymongo.ASCENDING)]),
            pymongo.IndexModel([("client_event_id", pymongo.ASCENDING)]),
        ]
