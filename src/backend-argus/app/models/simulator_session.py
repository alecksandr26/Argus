from __future__ import annotations

from datetime import datetime
from typing import Optional

import pymongo

from .common import TimestampedDocument


class SimulatorSession(TimestampedDocument):
    """One root_admin-started cloud simulator run (see app/simulator_control.py).

    Not in the ER diagram — it exists only to enforce the cost guardrails: at most one open
    session at a time, and a monthly quota charged `requested_minutes` up front on every start
    (never refunded on an early stop, the simple conservative rule). `stopped_at is None` means
    the session is still open as far as the backend knows; `status()` closes it once the ECS
    task is gone or the session's time is up.
    """

    started_by: str
    started_at: datetime
    requested_minutes: int
    truck_count: int
    task_arn: str
    stopped_at: Optional[datetime] = None
    stop_reason: Optional[str] = None

    class Settings:
        name = "simulator_sessions"
        indexes = [
            pymongo.IndexModel([("started_at", pymongo.DESCENDING)]),
        ]
