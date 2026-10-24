from __future__ import annotations

from datetime import datetime
from typing import Optional

import pymongo

from ..geo import GeoJSONPoint
from .common import IncidentState, RouteStatus, TimestampedDocument


class Route(TimestampedDocument):
    id_driver: str
    id_truck: str
    origin_name: str
    destination_name: str
    destination_coordinates: GeoJSONPoint
    estimated_departure: datetime
    estimated_arrival: Optional[datetime] = None
    actual_departure: Optional[datetime] = None
    actual_arrival: Optional[datetime] = None
    operative_status: RouteStatus = RouteStatus.SCHEDULED

    # Incident state (docs/designs/final-tweaks-contracts.md, C3.5). Raised by medium/critical
    # alerts; lowered only by closing incidents or by an hour of only-OK GPS points. Every write
    # to these goes through `app.incidents` under the per-route lock, as a targeted `$set`.
    incident_state: IncidentState = IncidentState.FINE
    last_not_ok_at: Optional[datetime] = None
    # Internal (not in RouteOut): the start of the current run of OK points and the newest one.
    ok_since: Optional[datetime] = None
    last_ok_point_at: Optional[datetime] = None
    # Per-route lock serializing incident grouping and route-state updates across requests (and
    # Lambda containers): taken with an atomic findOneAndUpdate, see `app.incidents.route_lock`.
    lock_token: Optional[str] = None
    lock_until: Optional[datetime] = None

    # Set by `POST /api/routes/{id}/stop` (status `cancelled`).
    stop_reason: Optional[str] = None
    stopped_by: Optional[str] = None  # User id
    stopped_at: Optional[datetime] = None

    class Settings:
        name = "routes"
        indexes = [
            pymongo.IndexModel([("id_truck", pymongo.ASCENDING)]),
            pymongo.IndexModel([("id_driver", pymongo.ASCENDING)]),
            pymongo.IndexModel([("operative_status", pymongo.ASCENDING)]),
            pymongo.IndexModel([("destination_coordinates", pymongo.GEOSPHERE)]),
        ]
