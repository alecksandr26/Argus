from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Optional

from pydantic import AfterValidator, BaseModel

from app.constants import ALERT_PHONE_PATTERN
from app.models.common import TruckStatus
from app.schemas.common import UtcDatetime


def _alert_phone(value: Optional[str]) -> Optional[str]:
    """E.164 or nothing (grip-alarm contracts, C1.1): `""` means "no phone" and is stored as
    None, so on an update both `null` and `""` clear it. Anything else that isn't E.164 is a 422,
    since a phone the ESP32 can't dial would silently drop every SMS."""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if not re.fullmatch(ALERT_PHONE_PATTERN, value):
        raise ValueError("alert_phone must be in E.164 format, e.g. +523312345678")
    return value


AlertPhone = Annotated[Optional[str], AfterValidator(_alert_phone)]


class TruckCreate(BaseModel):
    plate_number: str
    brand: str
    model: str
    company_number: str
    raspberry_pi_mac: Optional[str] = None
    esp32_id: Optional[str] = None
    operative_status: TruckStatus = TruckStatus.INACTIVE
    alert_phone: AlertPhone = None


class TruckUpdate(BaseModel):
    plate_number: Optional[str] = None
    brand: Optional[str] = None
    model: Optional[str] = None
    company_number: Optional[str] = None
    raspberry_pi_mac: Optional[str] = None
    esp32_id: Optional[str] = None
    operative_status: Optional[TruckStatus] = None
    alert_phone: AlertPhone = None


class TruckOut(BaseModel):
    id_truck: str
    plate_number: str
    brand: str
    model: str
    company_number: str
    raspberry_pi_mac: Optional[str]
    esp32_id: Optional[str]
    operative_status: TruckStatus
    alert_phone: Optional[str] = None
    # Device-credential status only -- never the key or its hash.
    has_device_key: bool = False
    device_key_issued_at: Optional[UtcDatetime] = None
    device_last_seen_at: Optional[UtcDatetime] = None
    created_at: UtcDatetime
    updated_at: UtcDatetime
