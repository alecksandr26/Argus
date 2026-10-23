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
    """The four actor roles.

    `ROOT_ADMIN`/`GUARDIAN`/`TRUCK_DRIVER` read directly off the root CLAUDE.md's original
    "Three actor roles" bullet. `ADMIN` was added later, deliberately: an operations role
    (schedules routes, manages the truck/driver roster) distinct from `ROOT_ADMIN` (the
    owner/bootstrap account, the only role with user-management access) and from `GUARDIAN`
    (read-only safety monitoring + alert review). `ADMIN` has a narrow, scoped exception to
    "no user-management access": it may create/manage `GUARDIAN`-role accounts only — see
    `app/routers/users.py`'s per-route scoping for the enforcement. No design doc predates this
    role; it was a deliberate decision, not a diagram correction like the other three names.
    """

    ROOT_ADMIN = "root_admin"
    ADMIN = "admin"
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

    @property
    def rank(self) -> int:
        """Least-severe -> most-severe, for comparisons (e.g. a de-escalation must go strictly
        down). The enum's declaration order is most-severe-first, so don't compare by that."""
        return _SEVERITY_RANK[self]


_SEVERITY_RANK = {Severity.LOW: 0, Severity.MEDIUM: 1, Severity.CRITICAL: 2}


class AlertSource(str, Enum):
    """Which decision path produced an `Alert`. Every new alert is `fusion`: the Pi's
    drowsiness rules, relayed by the ESP32 (`POST /api/devices/me/report`). `panic_button` is
    the retired panic button: old rows keep it and stay readable, but creating one is a 422
    (docs/designs/final-tweaks-contracts.md, C2.4)."""

    FUSION = "fusion"
    PANIC_BUTTON = "panic_button"


class Vigilance(str, Enum):
    """`StatusRoute.vigilance`: the `Severity` scale plus `unknown` (no recent Pi reading on the
    ESP32, or a legacy status post without the field). `unknown` is the default and never counts
    as "OK" for clearing a route's `incident_state` (C3.5)."""

    CRITICAL = "critical"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class IncidentState(str, Enum):
    """`Route.incident_state`: drives the map marker colour (C3.1, C3.5)."""

    FINE = "fine"
    MEDIUM = "medium"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return _INCIDENT_STATE_RANK[self]

    @classmethod
    def from_severity(cls, severity: "Severity") -> "IncidentState":
        if severity == Severity.CRITICAL:
            return cls.CRITICAL
        if severity == Severity.MEDIUM:
            return cls.MEDIUM
        return cls.FINE


_INCIDENT_STATE_RANK = {IncidentState.FINE: 0, IncidentState.MEDIUM: 1, IncidentState.CRITICAL: 2}


class Delivery(str, Enum):
    """How an alert reached the backend: `live`, or `dlq` = replayed from the Pi's dead-letter
    queue after a lost connection ("recovered after lost connection" in the UI)."""

    LIVE = "live"
    DLQ = "dlq"
