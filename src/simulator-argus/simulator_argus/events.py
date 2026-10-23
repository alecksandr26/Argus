"""The Pi side of a virtual truck: severity readings in, backend events out.

A scenario produces one `Reading` per tick, the way the Pi's rule engine holds one severity and
reason at a time. `EpisodeTracker` turns the *changes* of that severity into backend events
with the exact rule both real devices use (contracts C1.5):

| Event          | Condition (prev -> sev)                    |
|----------------|--------------------------------------------|
| alert medium   | sev == medium and rank(prev) < 2           |
| alert critical | sev == critical and rank(prev) < 3         |
| driver OK      | rank(sev) <= 1 and rank(prev) >= 2         |
| (none)         | everything else, incl. critical -> medium  |

So an incident produces one medium alert, at most one critical upgrade, and one OK when the
driver recovers, all under one `episode` id (12 lowercase hex chars, like the Pi's). A reason
change at the same severity produces nothing.
"""
from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from typing import Callable, Optional

from .constants import SIMULATOR_MODEL_NAME

RANK: dict[str, int] = {"none": 0, "low": 1, "medium": 2, "critical": 3}


def rank(severity: Optional[str]) -> int:
    return RANK.get(severity or "none", 0)


@dataclass(frozen=True)
class Reading:
    """One tick of the simulated Pi: its held severity, the reason behind it, and the
    `p(Drowsy)` score the event's `ai_metadata` reports (`None` = no scores, e.g. no face)."""

    severity: str
    reason: str
    drowsy_score: Optional[float] = None


def classify_transition(prev: Optional[str], sev: str) -> Optional[tuple[str, Optional[str]]]:
    """C1.5 as a pure function: `("alert", "medium"|"critical")`, `("ok", None)`, or `None`."""
    prev_rank, sev_rank = rank(prev), rank(sev)
    if sev == "medium" and prev_rank < 2:
        return ("alert", "medium")
    if sev == "critical" and prev_rank < 3:
        return ("alert", "critical")
    if sev_rank <= 1 and prev_rank >= 2:
        return ("ok", None)
    return None


def vigilance_for(severity: Optional[str]) -> str:
    """`points[].vigilance` from the latest Pi severity (C2.1): none/low -> low."""
    if severity in ("medium", "critical"):
        return severity
    if severity in ("none", "low"):
        return "low"
    return "unknown"


@dataclass(frozen=True)
class DeviceEvent:
    """A backend event as the device composed it, at the moment it happened. `occurred_at` is a
    monotonic timestamp (seconds): the wire carries only relative times (`age_s`)."""

    client_event_id: str
    episode: str
    kind: str  # "alert" | "ok"
    severity: Optional[str]  # "medium" | "critical" for an alert, None for ok
    alert_type: Optional[str]  # the Pi reason for an alert, None for ok
    occurred_at: float
    position: Optional[dict]  # {lat, lon, last_known, location_age_s} or None (unknown)
    speed: Optional[float]
    ai_metadata: Optional[dict]

    @property
    def priority_key(self) -> str:
        """`critical` | `medium` | `ok`: what the live retry window and priority are keyed on."""
        return "ok" if self.kind == "ok" else (self.severity or "medium")

    def to_wire(self, now: float, delivery: str = "live", queued_seconds: float = 0.0) -> dict:
        """The C2.1 `event` object, with `age_s` measured from `now` (same monotonic clock)."""
        return {
            "client_event_id": self.client_event_id,
            "episode": self.episode,
            "kind": self.kind,
            "severity": self.severity,
            "alert_type": self.alert_type,
            "age_s": round(max(0.0, now - self.occurred_at), 1),
            "delivery": delivery,
            "queued_seconds": round(max(0.0, queued_seconds), 1) if delivery == "dlq" else 0,
            "position": self.position,
            "speed": self.speed,
            "ai_metadata": self.ai_metadata,
        }


def _ai_metadata(drowsy_score: Optional[float]) -> dict:
    if drowsy_score is None:
        return {"model": SIMULATOR_MODEL_NAME, "scores": None}
    drowsy = round(min(1.0, max(0.0, drowsy_score)), 2)
    return {
        "model": SIMULATOR_MODEL_NAME,
        "scores": {"not_drowsy": round(1.0 - drowsy, 2), "drowsy": drowsy},
    }


def _new_event_id() -> str:
    return uuid.uuid4().hex


def _new_episode() -> str:
    return secrets.token_hex(6)


class EpisodeTracker:
    """Holds the previous severity and the open episode; `update()` returns the event a
    severity change produces, if any. The episode opens when the severity reaches `medium` (or
    jumps straight to `critical`) and is carried until and including the OK that closes it."""

    def __init__(
        self,
        event_id_factory: Callable[[], str] = _new_event_id,
        episode_factory: Callable[[], str] = _new_episode,
    ) -> None:
        self._event_id_factory = event_id_factory
        self._episode_factory = episode_factory
        self._severity = "none"
        self._episode: Optional[str] = None

    @property
    def severity(self) -> str:
        return self._severity

    @property
    def episode(self) -> Optional[str]:
        return self._episode

    def update(
        self,
        reading: Reading,
        now: float,
        position: Optional[dict] = None,
        speed: Optional[float] = None,
    ) -> Optional[DeviceEvent]:
        prev, sev = self._severity, reading.severity
        if sev not in RANK:
            raise ValueError(f"unknown severity {sev!r}")
        self._severity = sev
        if sev == prev:
            return None  # a keepalive or a reason change at the same level: never an event

        if rank(sev) >= 2 and self._episode is None:
            self._episode = self._episode_factory()

        kind = classify_transition(prev, sev)
        episode = self._episode
        if rank(sev) <= 1:
            self._episode = None  # the recovery record is the last one to carry the episode
        if kind is None or episode is None:
            return None

        event_kind, severity = kind
        if event_kind == "ok":
            return DeviceEvent(
                client_event_id=self._event_id_factory(),
                episode=episode,
                kind="ok",
                severity=None,
                alert_type=None,
                occurred_at=now,
                position=position,
                speed=speed,
                ai_metadata=None,
            )
        return DeviceEvent(
            client_event_id=self._event_id_factory(),
            episode=episode,
            kind="alert",
            severity=severity,
            alert_type=reading.reason,
            occurred_at=now,
            position=position,
            speed=speed,
            ai_metadata=_ai_metadata(reading.drowsy_score),
        )
