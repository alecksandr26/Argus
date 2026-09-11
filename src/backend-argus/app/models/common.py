"""Shared mixin + enums for the six Beanie Documents.

Field-naming note: every enum value and field name here is the ER diagram
(`docs/designs/ER-model.drawio.xml`) with a handful of typos/inconsistencies fixed — see this
module's CLAUDE.md for the full old-name -> new-name table. Treat this file (and the sibling
model files), not the diagram, as the live source of truth going forward.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from beanie import Document
from pydantic import Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampedDocument(Document):
    """Adds `created_at`/`updated_at`, set/refreshed automatically on insert/replace.

    The ER diagram spells this field `update_at` on some entities and `updated_at` on others —
    normalized to `updated_at` everywhere here, matching what `ui-argus/src/types.ts` already
    uses for every entity except `User` (which didn't have the field in the diagram at all;
    added here for parity, per the backend plan).
    """

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    class Settings:
        is_root = False

    async def touch_and_save(self, **kwargs) -> None:
        """Bump `updated_at` and persist — call this instead of a bare `.save()` on update."""
        self.updated_at = _utcnow()
        await self.save(**kwargs)


class Role(str, Enum):
    """The three actor roles named in the root CLAUDE.md's "Three actor roles" bullet.

    Replaces `ui-argus`'s placeholder `'guard' | 'admin'` guess (its `src/types.ts` doc comment
    flagged these as unreconciled with the backend) — these three names read directly off the
    root CLAUDE.md prose rather than inventing new terms.
    """

    ROOT_ADMIN = "root_admin"
    GUARDIAN = "guardian"
    TRUCK_DRIVER = "truck_driver"


class TruckStatus(str, Enum):
    ACTIVE = "active"
    ALERT = "alert"
    MAINTENANCE = "maintenance"
    INACTIVE = "inactive"


class DriverStatus(str, Enum):
    ON_ROUTE = "on_route"
    ON_ROUTE_ALERT = "on_route_alert"
    RESTING = "resting"
    INACTIVE = "inactive"


class RouteStatus(str, Enum):
    IN_PROGRESS = "in_progress"
    IN_PROGRESS_ALERT = "in_progress_alert"
    SCHEDULED = "scheduled"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class Vigilance(str, Enum):
    """Status_Route's live drowsiness reading.

    The ER diagram names this field `operative_status`, but that name is kept distinct here
    (as `vigilance`, matching `ui-argus/src/types.ts`) on purpose: `Truck`, `Driver`, and `Route`
    each already have their own, differently-enumerated `operative_status` field, and reusing
    the name for a fourth, unrelated enum on `Status_Route` would be a real ambiguity, not just
    a style choice.
    """

    NORMAL = "normal"
    LOW_VIGILANCE = "low_vigilance"
    CRITICAL = "critical"


class AlertSeverity(str, Enum):
    CRITICAL = "critical"
    MEDIUM = "medium"
    LOW = "low"
