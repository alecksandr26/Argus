"""The combined report (`POST /api/devices/me/report`, contracts C2.1) and how the device
reads its answer (C2.2/C2.3).

One report = the GPS points read since the previous report (oldest first, the newest
`REPORT_MAX_POINTS` kept) plus at most one event. Points of a report are dropped whatever its
outcome: a failed report leaves a gap in the path rather than being resent (plan section 1).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Optional

from .constants import MAX_POINT_AGE_S, REPORT_MAX_POINTS


@dataclass(frozen=True)
class GpsPoint:
    """One GPS read. `t` is a monotonic timestamp (seconds) on the same clock as events."""

    t: float
    lat: float
    lon: float
    speed: float
    vigilance: str  # low | medium | critical | unknown
    last_known: bool = False
    location_age_s: float = 0.0

    def to_wire(self, now: float) -> dict:
        return {
            "age_s": round(min(MAX_POINT_AGE_S, max(0.0, now - self.t)), 1),
            "lat": self.lat,
            "lon": self.lon,
            "speed": 0.0 if self.last_known else round(max(0.0, self.speed), 1),
            "vigilance": self.vigilance,
            "last_known": self.last_known,
            "location_age_s": round(max(0.0, self.location_age_s), 1),
        }


class PointBuffer:
    """GPS points since the last report; only the newest `REPORT_MAX_POINTS` are kept."""

    def __init__(self, max_points: int = REPORT_MAX_POINTS) -> None:
        self._points: deque[GpsPoint] = deque(maxlen=max_points)

    def add(self, point: GpsPoint) -> None:
        self._points.append(point)

    def drain(self) -> list[GpsPoint]:
        points = list(self._points)
        self._points.clear()
        return points

    def __len__(self) -> int:
        return len(self._points)


def build_report(points: list[GpsPoint], event: Optional[dict], now: float) -> dict:
    """The request body. `event` is an already-built C2.1 event object (or `None`)."""
    body: dict = {"points": [p.to_wire(now) for p in sorted(points, key=lambda p: p.t)]}
    if event is not None:
        body["event"] = event
    return body


@dataclass(frozen=True)
class ReportOutcome:
    """What one report attempt means for the device.

    - `event_result`: `delivered` (created/duplicate), `rejected` (stop retrying, `event_reason`
      says why), `failed` (an attempt that counts; retry rules apply), or `None` when the
      report carried no event / the answer says nothing about it.
    - `route_known`: `True` only on a 200; then `active_route` is the backend's answer (which
      may be `None`) and `points_status` its `points.status`.
    - `reconfigure`: a 401 (the device would re-`CONFIG` from the Pi).
    """

    http_status: Optional[int]
    event_result: Optional[str]
    event_reason: Optional[str] = None
    route_known: bool = False
    active_route: Optional[str] = None
    points_status: Optional[str] = None
    reconfigure: bool = False

    @property
    def route_finished(self) -> bool:
        return self.points_status == "route_finished" or (
            self.event_result == "rejected" and self.event_reason == "route_finished"
        )


def classify_response(
    status_code: int, body: Optional[dict], had_event: bool
) -> ReportOutcome:
    """C2.3, row by row."""
    if status_code == 200 and isinstance(body, dict):
        event_part = body.get("event") if had_event else None
        event_result: Optional[str] = None
        reason: Optional[str] = None
        if had_event:
            status = (event_part or {}).get("status")
            if status in ("created", "duplicate"):
                event_result = "delivered"
            elif status == "rejected":
                event_result = "rejected"
                reason = (event_part or {}).get("reason") or "invalid"
            else:
                # A 200 that doesn't answer for the event we sent: treat as a failed attempt
                # rather than guess it was stored.
                event_result, reason = "failed", "bad_response"
        points = body.get("points") or {}
        return ReportOutcome(
            http_status=200,
            event_result=event_result,
            event_reason=reason,
            route_known=True,
            active_route=body.get("active_route"),
            points_status=points.get("status"),
        )
    if status_code == 200:
        return _failed(200, "bad_response", had_event)
    if status_code == 401:
        return ReportOutcome(
            http_status=401,
            event_result="failed" if had_event else None,
            event_reason="http_401" if had_event else None,
            reconfigure=True,
        )
    if status_code in (413, 422):
        # A malformed report is a bug on our side: the event is treated as `rejected invalid`.
        return ReportOutcome(
            http_status=status_code,
            event_result="rejected" if had_event else None,
            event_reason="invalid" if had_event else None,
        )
    return _failed(status_code, f"http_{status_code}", had_event)


def classify_transport_error(reason: str, had_event: bool) -> ReportOutcome:
    """A request that never got an HTTP answer: `timeout`, `transport_error`, `no_network`."""
    return _failed(None, reason, had_event)


def _failed(status: Optional[int], reason: str, had_event: bool) -> ReportOutcome:
    return ReportOutcome(
        http_status=status,
        event_result="failed" if had_event else None,
        event_reason=reason if had_event else None,
    )

