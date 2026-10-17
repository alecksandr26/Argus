"""Fields every device-ingested record (Alert, Status_Route) shares: retry dedup, event time,
and where the position came from.

The ESP32 relays records that may be minutes old (a Bluetooth or network outage leaves a
backlog), may send the same one twice (a lost HTTP response or Bluetooth ACK), and since the
GPRS/cell-location work may attach an approximate cell-tower position instead of GPS. See
docs/designs/edge-cloud-scenarios.md: BE-06, BE-07, BE-08.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

# How far a device clock may run ahead of the server before an event time is rejected, and how
# old a relayed backlog record may be. A week covers a truck offline over a long weekend.
MAX_FUTURE_SKEW = timedelta(minutes=5)
MAX_EVENT_AGE = timedelta(days=7)

TimeSource = Literal["gps", "network", "server"]
LocationSource = Literal["gps", "cell"]


class IngestionProvenance(BaseModel):
    # The Pi record id (or an ESP32-generated id for panic events). A retry with the same id
    # returns the row already stored instead of creating a duplicate.
    client_event_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    # When the event happened, from the device's GPS (or network) clock. Absent: server time.
    occurred_at: Optional[datetime] = None
    time_source: Optional[TimeSource] = None
    location_source: LocationSource = "gps"
    location_accuracy_m: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _check_event_time(self):
        if self.occurred_at is None:
            if self.time_source not in (None, "server"):
                raise ValueError("time_source given without occurred_at")
            self.time_source = "server"
            return self
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must include a timezone (UTC)")
        now = datetime.now(timezone.utc)
        if self.occurred_at > now + MAX_FUTURE_SKEW:
            raise ValueError("occurred_at is in the future")
        if self.occurred_at < now - MAX_EVENT_AGE:
            raise ValueError("occurred_at is older than the accepted backlog window")
        if self.time_source is None:
            self.time_source = "gps"
        elif self.time_source == "server":
            raise ValueError("time_source 'server' can't come with a device occurred_at")
        return self

    def event_time(self) -> datetime:
        return self.occurred_at or datetime.now(timezone.utc)
