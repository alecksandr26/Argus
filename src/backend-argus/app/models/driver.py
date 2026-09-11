from __future__ import annotations

from datetime import date

from .common import DriverStatus, TimestampedDocument


class Driver(TimestampedDocument):
    first_name: str
    last_name: str
    license_number: str
    license_expiration: date
    phone_number: str
    emergency_contact_name: str
    emergency_contact_phone: str
    operative_status: DriverStatus = DriverStatus.INACTIVE
    # ER diagram spells this `blod_type` — fixed here, see models/common.py's module docstring.
    blood_type: str

    class Settings:
        name = "drivers"
