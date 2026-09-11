from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel

from app.models.common import DriverStatus


class DriverCreate(BaseModel):
    first_name: str
    last_name: str
    license_number: str
    license_expiration: date
    phone_number: str
    emergency_contact_name: str
    emergency_contact_phone: str
    blood_type: str
    operative_status: DriverStatus = DriverStatus.INACTIVE


class DriverUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    license_number: Optional[str] = None
    license_expiration: Optional[date] = None
    phone_number: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    blood_type: Optional[str] = None
    operative_status: Optional[DriverStatus] = None


class DriverOut(BaseModel):
    id_driver: str
    first_name: str
    last_name: str
    license_number: str
    license_expiration: date
    phone_number: str
    emergency_contact_name: str
    emergency_contact_phone: str
    blood_type: str
    operative_status: DriverStatus
    created_at: datetime
    updated_at: datetime
