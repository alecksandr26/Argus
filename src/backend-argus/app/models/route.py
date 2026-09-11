from __future__ import annotations

from datetime import datetime
from typing import Optional

import pymongo

from ..geo import GeoJSONPoint
from .common import RouteStatus, TimestampedDocument


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

    class Settings:
        name = "routes"
        indexes = [
            pymongo.IndexModel([("id_truck", pymongo.ASCENDING)]),
            pymongo.IndexModel([("id_driver", pymongo.ASCENDING)]),
            pymongo.IndexModel([("operative_status", pymongo.ASCENDING)]),
            pymongo.IndexModel([("destination_coordinates", pymongo.GEOSPHERE)]),
        ]
