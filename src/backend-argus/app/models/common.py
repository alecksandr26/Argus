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


class Severity(str, Enum):
    """The one shared severity scale for both `Status_Route.vigilance` (a live, continuous
    reading) and `Alert.severity_level` (a discrete event) — previously two separately-named,
    mismatched 3-tier enums (`Vigilance`: `normal`/`low_vigilance`/`critical`, and this class's
    old name `AlertSeverity`: `critical`/`medium`/`low`), unified so status and alerts can
    actually be compared on one scale. `Status_Route` keeps the field name `vigilance` (see
    the backend CLAUDE.md's "Why `Status_Route.vigilance`, not `operative_status`" section for
    why that name itself is kept distinct from `Truck`/`Driver`/`Route`'s own `operative_status`
    fields) — only the *type* is now shared, not the field name.

    Retired `Vigilance`'s values map onto this one order-preserving (least-severe ->
    most-severe): `normal -> low`, `low_vigilance -> medium`, `critical -> critical`. Any old
    Mongo document still carrying a `Vigilance` string needs that translation applied by hand.
    """

    CRITICAL = "critical"
    MEDIUM = "medium"
    LOW = "low"


class AlertSource(str, Enum):
    """Which decision path produced an `Alert`. Every alert is either the ESP32's fused
    drowsy+grip evaluation, or the panic button (unconditional `critical`, no debounce) — see
    `src/esp32-argus/README.md`'s fusion section and `src/cv-argus/src/orchestrator/
    fusion_contract.py`'s reference decision logic. `ai_metadata`/`grip_status` on `Alert` are
    required together when `source == FUSION`, and both `None` when `source == PANIC_BUTTON`.
    """

    FUSION = "fusion"
    PANIC_BUTTON = "panic_button"
