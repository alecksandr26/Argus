from __future__ import annotations

from typing import Optional

import pymongo

from .common import TimestampedDocument, TruckStatus


class Truck(TimestampedDocument):
    plate_number: str
    brand: str
    model: str
    company_number: str
    raspberry_pi_mac: Optional[str] = None
    esp32_id: Optional[str] = None
    operative_status: TruckStatus = TruckStatus.INACTIVE

    # Not in the ER diagram — added so the ESP32 can authenticate its writes without a full
    # user login (see the backend CLAUDE.md's "Auth design" section). bcrypt-hashed, never
    # returned by any endpoint; the plaintext key is shown exactly once, at generation time.
    device_api_key_hash: Optional[str] = None

    class Settings:
        name = "trucks"
        indexes = [
            pymongo.IndexModel([("plate_number", pymongo.ASCENDING)], unique=True),
        ]
