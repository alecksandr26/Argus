from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class SimulatorStartRequest(BaseModel):
    """Deliberately has no duration field: every session is the server's fixed
    `SIMULATOR_SESSION_MINUTES`. `extra="forbid"` makes sending one (e.g. `minutes`) a 422
    rather than silently ignored, so nobody believes they asked for a longer run."""

    model_config = ConfigDict(extra="forbid")

    truck_count: int = Field(default=6, ge=1, le=10)


class SimulatorStatusOut(BaseModel):
    state: Literal["stopped", "starting", "running"]
    session_minutes: int
    started_at: Optional[datetime]
    auto_off_at: Optional[datetime]
    truck_count: Optional[int]
    quota_minutes: int
    quota_used_minutes: int
    quota_remaining_minutes: int
    sessions_remaining: int
