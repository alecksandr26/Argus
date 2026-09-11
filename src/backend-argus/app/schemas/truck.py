from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.models.common import TruckStatus


class TruckCreate(BaseModel):
    plate_number: str
    brand: str
    model: str
    company_number: str
    raspberry_pi_mac: Optional[str] = None
    esp32_id: Optional[str] = None
    operative_status: TruckStatus = TruckStatus.INACTIVE


class TruckUpdate(BaseModel):
    plate_number: Optional[str] = None
    brand: Optional[str] = None
    model: Optional[str] = None
    company_number: Optional[str] = None
    raspberry_pi_mac: Optional[str] = None
    esp32_id: Optional[str] = None
    operative_status: Optional[TruckStatus] = None


class TruckOut(BaseModel):
    id_truck: str
    plate_number: str
    brand: str
    model: str
    company_number: str
    raspberry_pi_mac: Optional[str]
    esp32_id: Optional[str]
    operative_status: TruckStatus
    created_at: datetime
    updated_at: datetime
