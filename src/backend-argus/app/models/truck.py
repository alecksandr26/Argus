from __future__ import annotations

from datetime import datetime
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
    # SHA-256 of the same key (`app.auth.security.key_fingerprint`), indexed, so a device can be
    # identified from its key alone. None for keys issued before this field existed.
    device_api_key_fingerprint: Optional[str] = None
    # When the current key was issued (rotate-key), and when a request last authenticated with
    # it -- what the dashboard's "Onboard device" panel shows. Both None until a key exists.
    device_key_issued_at: Optional[datetime] = None
    device_last_seen_at: Optional[datetime] = None

    class Settings:
        name = "trucks"
        indexes = [
            pymongo.IndexModel([("plate_number", pymongo.ASCENDING)], unique=True),
            pymongo.IndexModel([("device_api_key_fingerprint", pymongo.ASCENDING)]),
        ]
